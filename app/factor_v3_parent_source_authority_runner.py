"""Single-attempt runner for the frozen Factor V2 parent source verifiers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence


RUN_SPEC_SCHEMA = "factor-v3-parent-source-candidate-replay-run-spec/v1"
STATUS_SCHEMA = "factor-v3-parent-source-candidate-replay-run-status/v1"
CLAIM_SCHEMA = "factor-v3-parent-source-candidate-replay-single-attempt-claim/v1"
CHILD_INPUT_SCHEMA = "factor-v3-parent-source-child-input/v1"
CHILD_RESULT_SCHEMA = "factor-v3-parent-source-public-verification/v1"
RECEIPT_SCHEMA = "factor-v3-parent-source-candidate-replay-receipt/v1"
FAILURE_SCHEMA = "factor-v3-parent-source-candidate-replay-failure/v1"
RESULT_SCHEMA = "factor-v3-parent-source-candidate-replay-run-result/v1"
RUNTIME_DEPENDENCY_MANIFEST_SCHEMA = (
    "factor-v3-parent-source-candidate-runtime-file-closure/v1"
)
VERIFICATION_CLAIM_SCHEMA = (
    "factor-v3-parent-source-candidate-independent-verification-claim/v1"
)
VERIFICATION_STATUS_SCHEMA = (
    "factor-v3-parent-source-candidate-independent-verification-status/v1"
)
VERIFICATION_RECEIPT_SCHEMA = (
    "factor-v3-parent-source-candidate-independent-verification-receipt/v1"
)
VERIFICATION_RESULT_SCHEMA = (
    "factor-v3-parent-source-candidate-independent-verification-result/v1"
)
VERIFICATION_FAILURE_SCHEMA = (
    "factor-v3-parent-source-candidate-independent-verification-failure/v1"
)

FROZEN_SOURCE_COMMIT = "3e9bd1bcf12024f9bf52a0b0fbcdd86f7bc64109"
FROZEN_SOURCE_TREE_OID = "bf378b4e23436e5e6fa2e9d35ba14f8ab4495c96"
FROZEN_SOURCE_BLOB_SHA256 = {
    "app/audited_pit_continuous_ridge_oof.py": (
        "6f51dca47e2fe5816b540f1a99b947b3cb8dc3f44e27306580814ea54ee91c47"
    ),
    "app/audited_pit_factor_v2.py": (
        "9a480e86ee8b462e175c0499fbcfe810e50622435cced767aff8b37b50edebd9"
    ),
    "app/audited_pit_factor_v2_parent.py": (
        "3e4c4e3bbaba96b784f2161ad055f0864360adbe00bf4fd5dde40d4dd13a8522"
    ),
    "app/audited_pit_factor_v2_runtime.py": (
        "efeaf00fda5d064e09a4faa4932e5b77487d79df476be6a56a415a92c7865126"
    ),
    "app/audited_pit_factor_v2_suspension.py": (
        "92ce088d0c5ae7012be77279aa7cd777e4c7dd69a639b80307f72f4bd4c107d4"
    ),
    "app/audited_pit_factor_v2_training_overlay.py": (
        "a8bae99348d28555e77fd90d0ad674bc2f926c2d994f23275433e6b02b360a21"
    ),
    "app/audited_pit_ranked_liquidity_store.py": (
        "f0ec56877973ee1fbcaec20d75999783f1d46cea60b14b8f80d34442bb9499d7"
    ),
    "app/audited_pit_shallow_gbdt.py": (
        "1a41d9e75c42b2eadd575a21ef3e2a1fe00f41e16f9a190987cf9371b4100d7c"
    ),
    "app/audited_pit_training_dataset_materializer.py": (
        "88d8ada6e25bc5fcb6b56f4825fb75914208102d592dd60044cc02c388948c95"
    ),
    "app/audited_pit_training_dataset_store.py": (
        "fe886d4799a74727f9ac421714fe1f0bd917ce8fa3e511c5bf7c16e2b2655171"
    ),
    "app/durable_io.py": (
        "afc983c24e0437b0c1db23b57577c141f6aa9faec84d3a7f7a82723fa5fb8c7f"
    ),
    "app/research_suspension_evidence.py": (
        "20273a95ffa5cbaacc107fd02fbfb9ebfba7fa3d97965d20daf401b1c1808f08"
    ),
}

PARENT_ARTIFACT_SHA256 = "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
PARENT_MANIFEST_FILE_SHA256 = (
    "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
)
OVERLAY_ARTIFACT_SHA256 = "abd4b2166da4520952d7bfc5c8a988bc0a8027dd576a90ae0fdda2560b3a02f8"
OVERLAY_MANIFEST_FILE_SHA256 = (
    "9131f15e13f247a4663fae658af544b94bf2af01a3494b8eb0a3d0c095ee1312"
)
FACTOR_V2_SPEC_SHA256 = "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
SUSPENSION_BUNDLE_SHA256 = "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4"
SUSPENSION_METADATA_SHA256 = (
    "0400502a53aa8ec3b645d4761eb744bdafccd395dc94e8939f4ee23071b950a5"
)
PARENT_FEATURE_ROW_COUNT = 1_796_835
PARENT_OUTCOME_ROW_COUNT = 1_782_860
PARENT_FOLD_COUNT = 6
PARENT_FOLDS_SHA256 = "f60ff439951a5125a8aa72ae8c9974dc775fe67cee56cab5388bcaf7a66a62db"
OVERLAY_ELIGIBLE_CANDIDATE_COUNT = 1_796_834
OVERLAY_EXCLUDED_CANDIDATE_COUNT = 1
CHECKOUT_POLICY = "git-lf-blob-to-windows-crlf/v1"
CHILD_TIMEOUT_SECONDS = 86_400
IMPORT_SMOKE_TIMEOUT_SECONDS = 120
_PYCACHE_PREFIX_TOKEN = "<runtime-pycache-prefix>"
_CREATE_SUSPENDED = 0x00000004
_REQUIRED_RUNTIME_MODULES = ("numpy", "pandas", "requests", "xgboost")
_ALLOWED_IMPORT_NETWORK_EVENT_SEQUENCES = (
    ("gethostname", "new", "bind"),
    ("new", "bind", "gethostname"),
)

PINNED_PYTHON_EXECUTABLE = Path(sys.executable).resolve()
_GIT_DISCOVERY = shutil.which("git")
if _GIT_DISCOVERY is None:
    raise RuntimeError("pinned Git executable unavailable")
PINNED_GIT_EXECUTABLE = Path(_GIT_DISCOVERY).resolve()

PARENT_DATABASE_FILENAMES = (
    "ranked_liquidity_stage.sqlite3",
    "strict_execution_spool.sqlite3",
    "shallow_gbdt_training_dataset.sqlite3",
)
OVERLAY_DATABASE_FILENAME = "factor_v2_training_overlay.sqlite3"

SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "experiment_launch_eligible",
    "final_oos_consumed",
    "model_training_started",
    "oof_scoring_started",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "producer_binding_verified",
    "recommendation_generation_eligible",
    "runtime_dependency_authority_verified",
    "source_authority_complete",
)

_RUN_SPEC_FIELDS = frozenset(
    {
        "development_only",
        "external_claim_path_sha256",
        "formal_materialization_eligible",
        "run_root_sha256",
        "external_run_claim_path",
        "external_verification_claim_path",
        "frozen_source",
        "overlay",
        "parent",
        "run_spec_sha256",
        "run_root",
        "runtime_dependencies",
        "schema",
        "subprocess",
        "suspension",
        "verification_root",
        *SAFETY_FALSE_FIELDS,
    }
)
_FROZEN_FIELDS = frozenset(
    {"checkout_policy", "expected_blob_sha256", "expected_commit", "expected_tree_oid", "root"}
)
_PARENT_FIELDS = frozenset(
    {"expected_artifact_sha256", "expected_manifest_file_sha256", "manifest_path"}
)
_OVERLAY_FIELDS = frozenset(
    {
        "expected_artifact_sha256",
        "expected_factor_v2_spec_sha256",
        "expected_manifest_file_sha256",
        "manifest_path",
    }
)
_SUSPENSION_FIELDS = frozenset(
    {"expected_bundle_sha256", "expected_metadata_sha256", "metadata_path"}
)
_RUNTIME_DEPENDENCY_FIELDS = frozenset(
    {
        "allowed_import_network_event_sequences",
        "manifest_file_sha256",
        "manifest_path",
        "manifest_root_sha256",
        "required_modules",
        "site_packages_root",
    }
)
_SUBPROCESS_FIELDS = frozenset(
    {
        "child_result_schema",
        "environment",
        "max_stderr_bytes",
        "max_stdout_bytes",
        "network_allowed",
        "git_executable",
        "git_executable_sha256",
        "public_entrypoints",
        "python_executable",
        "python_executable_sha256",
        "python_flags",
        "pycache_policy",
        "research_execution_allowed",
        "schema",
        "source_import_mode",
        "timeout_seconds",
    }
)
_STATUS_FIELDS = frozenset(
    {
        "authority_status",
        "candidate_verified",
        "child_exit_code",
        "child_stderr",
        "child_stdout",
        "claim_sha256",
        "development_only",
        "failure",
        "formal_materialization_eligible",
        "receipt",
        "run_spec_sha256",
        "schema",
        "source_authority_verified",
        "status",
        "status_sha256",
        "verified",
        *SAFETY_FALSE_FIELDS,
    }
)
_SHA_RE = frozenset("0123456789abcdef")
_MAX_RUN_SPEC_BYTES = 256 * 1024
_MAX_STATE_BYTES = 512 * 1024
_MAX_RUNTIME_MANIFEST_BYTES = 16 * 1024 * 1024
_CHUNK_BYTES = 4 * 1024 * 1024
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class FactorV3ParentSourceAuthorityRunnerError(ValueError):
    """Raised when the frozen parent source run fails closed."""


class _ChildBoundaryError(FactorV3ParentSourceAuthorityRunnerError):
    def __init__(
        self,
        message: str,
        *,
        child_exit_code: int | None,
        child_stdout: Mapping[str, Any] | None = None,
        child_stderr: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.child_exit_code = child_exit_code
        self.child_stdout = child_stdout
        self.child_stderr = child_stderr


class _IndependentReplayError(FactorV3ParentSourceAuthorityRunnerError):
    def __init__(
        self,
        message: str,
        *,
        child_exit_code: int | None,
        child_stdout: Mapping[str, Any] | None,
        child_stderr: Mapping[str, Any] | None,
        preflight: Mapping[str, Any] | None,
        postflight: Mapping[str, Any] | None,
    ) -> None:
        super().__init__(message)
        self.child_exit_code = child_exit_code
        self.child_stdout = child_stdout
        self.child_stderr = child_stderr
        self.preflight = preflight
        self.postflight = postflight


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
        raise FactorV3ParentSourceAuthorityRunnerError("runner JSON rejected") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_sha256(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _SHA_RE for character in value)
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} SHA-256 rejected")
    return value


def _strict_oid(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 40
        or any(character not in _SHA_RE for character in value)
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} object ID rejected")
    return value


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise FactorV3ParentSourceAuthorityRunnerError("runner duplicate JSON key rejected")
        value[key] = item
    return value


def _strict_json(raw: bytes, *, label: str, max_bytes: int) -> dict[str, Any]:
    if not raw or len(raw) > max_bytes:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} size rejected")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} JSON rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} canonical JSON rejected")
    return value


def _assert_fields(value: Any, fields: frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} fields rejected")
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str or not Path(value).is_absolute():
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} path rejected")
    return Path(value)


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=left.exists())
    right_resolved = right.resolve(strict=right.exists())
    return (
        left_resolved == right_resolved
        or left_resolved.is_relative_to(right_resolved)
        or right_resolved.is_relative_to(left_resolved)
    )


def _assert_output_paths_disjoint(
    *,
    run_root: Path,
    verification_root: Path,
    external_run_claim: Path,
    external_verification_claim: Path,
    input_paths: Sequence[Path],
) -> None:
    if _paths_overlap(run_root, verification_root):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "run and verification roots overlap"
        )
    if _paths_overlap(external_run_claim, external_verification_claim):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "external claim paths overlap"
        )
    for claim in (external_run_claim, external_verification_claim):
        if _paths_overlap(claim, run_root) or _paths_overlap(claim, verification_root):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "claim and output roots overlap"
            )
    for output in (
        run_root,
        verification_root,
        external_run_claim,
        external_verification_claim,
    ):
        for input_path in input_paths:
            if _paths_overlap(output, input_path):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runner output overlaps read-only input"
                )


def _scope_false(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("development_only") is not True:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} development scope rejected")
    if value.get("formal_materialization_eligible") is not False:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} materialization scope rejected")
    if any(value.get(field) is not False for field in SAFETY_FALSE_FIELDS):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} scope rejected")


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_dependency_files(root: Path) -> list[dict[str, Any]]:
    _assert_safe_path(root, label="runtime dependency root", regular=False)
    pending = [root]
    files: list[dict[str, Any]] = []
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency tree scan rejected"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_reparse_point(path):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runtime dependency reparse point rejected"
                )
            if entry.is_dir(follow_symlinks=False):
                if entry.name == "__pycache__":
                    continue
                pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runtime dependency special path rejected"
                )
            if path.suffix.lower() in {".pyc", ".pyo"}:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runtime dependency sourceless bytecode rejected"
                )
            metadata = path.stat()
            files.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": _stream_sha256(path),
                    "size_bytes": metadata.st_size,
                }
            )
    return sorted(files, key=lambda item: item["relative_path"])


def build_factor_v3_runtime_dependency_manifest(
    site_packages_root: str | Path,
) -> dict[str, Any]:
    root = Path(site_packages_root)
    if not root.is_absolute() or root.name != "site-packages":
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency root contract rejected"
        )
    files = _runtime_dependency_files(root)
    if not files:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest is empty"
        )
    unsigned = {
        "allowed_import_network_event_sequences": [
            list(sequence) for sequence in _ALLOWED_IMPORT_NETWORK_EVENT_SEQUENCES
        ],
        "candidate_file_closure_only": True,
        "file_count": len(files),
        "files": files,
        "files_sha256": _canonical_sha256(files),
        "formal_execution_eligible": False,
        "native_dependencies_attested": False,
        "pth_processing_allowed": False,
        "python_runtime_attested": False,
        "pycache_policy": "unique-empty-prefix-and-source-cache-bypass/v1",
        "required_modules": list(_REQUIRED_RUNTIME_MODULES),
        "runtime_dependency_authority_verified": False,
        "schema": RUNTIME_DEPENDENCY_MANIFEST_SCHEMA,
        "site_packages_root": str(root),
        "venv_relationship_attested": False,
    }
    return _validated_runtime_dependency_manifest(
        {**unsigned, "manifest_root_sha256": _canonical_sha256(unsigned)}
    )


def _validated_runtime_dependency_manifest(value: Any) -> dict[str, Any]:
    fields = {
        "allowed_import_network_event_sequences",
        "candidate_file_closure_only",
        "file_count",
        "files",
        "files_sha256",
        "formal_execution_eligible",
        "manifest_root_sha256",
        "native_dependencies_attested",
        "pth_processing_allowed",
        "python_runtime_attested",
        "pycache_policy",
        "required_modules",
        "runtime_dependency_authority_verified",
        "schema",
        "site_packages_root",
        "venv_relationship_attested",
    }
    if type(value) is not dict or set(value) != fields:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest fields rejected"
        )
    root = _absolute_path(value["site_packages_root"], label="runtime dependency root")
    files = value["files"]
    if (
        value.get("schema") != RUNTIME_DEPENDENCY_MANIFEST_SCHEMA
        or value.get("allowed_import_network_event_sequences")
        != [list(sequence) for sequence in _ALLOWED_IMPORT_NETWORK_EVENT_SEQUENCES]
        or value.get("candidate_file_closure_only") is not True
        or value.get("formal_execution_eligible") is not False
        or value.get("native_dependencies_attested") is not False
        or root.name != "site-packages"
        or value.get("required_modules") != list(_REQUIRED_RUNTIME_MODULES)
        or value.get("pth_processing_allowed") is not False
        or value.get("python_runtime_attested") is not False
        or value.get("pycache_policy")
        != "unique-empty-prefix-and-source-cache-bypass/v1"
        or type(files) is not list
        or not files
        or value.get("file_count") != len(files)
        or value.get("runtime_dependency_authority_verified") is not False
        or value.get("venv_relationship_attested") is not False
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest contract rejected"
        )
    observed_paths: list[str] = []
    observed_casefold_paths: set[str] = set()
    for item in files:
        if type(item) is not dict or set(item) != {
            "relative_path",
            "sha256",
            "size_bytes",
        }:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency file binding rejected"
            )
        relative = item["relative_path"]
        parts = relative.split("/") if type(relative) is str else []
        if (
            type(relative) is not str
            or not relative
            or "\\" in relative
            or ":" in relative
            or Path(relative).is_absolute()
            or any(
                not part
                or part in {".", ".."}
                or part.endswith((" ", "."))
                for part in parts
            )
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] < 0
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency file path rejected"
            )
        _strict_sha256(item["sha256"], label="runtime dependency file")
        folded = relative.casefold()
        if folded in observed_casefold_paths:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency case-folded path collision rejected"
            )
        observed_casefold_paths.add(folded)
        observed_paths.append(relative)
    if observed_paths != sorted(set(observed_paths)):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency file exact set rejected"
        )
    if value.get("files_sha256") != _canonical_sha256(files):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency files root rejected"
        )
    unsigned = dict(value)
    observed_root = _strict_sha256(
        unsigned.pop("manifest_root_sha256"),
        label="runtime dependency manifest",
    )
    if observed_root != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest self hash rejected"
        )
    return json.loads(_canonical_bytes(value))


def _subprocess_contract(python_executable: Path) -> dict[str, Any]:
    return {
        "child_result_schema": CHILD_RESULT_SCHEMA,
        "environment": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "inherited_environment_allowed": False,
        },
        "max_stderr_bytes": 1024 * 1024,
        "max_stdout_bytes": 128 * 1024,
        "network_allowed": False,
        "git_executable": str(PINNED_GIT_EXECUTABLE),
        "git_executable_sha256": _stream_sha256(PINNED_GIT_EXECUTABLE),
        "public_entrypoints": [
            "app.audited_pit_factor_v2_parent.verify_preregistered_development_4_parent",
            (
                "app.audited_pit_factor_v2_training_overlay."
                "verify_preregistered_development_4_factor_v2_training_overlay"
            ),
        ],
        "python_executable": str(python_executable),
        "python_executable_sha256": _stream_sha256(python_executable),
        "python_flags": [
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={_PYCACHE_PREFIX_TOKEN}",
        ],
        "pycache_policy": "unique-empty-prefix-and-source-cache-bypass/v1",
        "research_execution_allowed": False,
        "schema": "factor-v3-parent-source-isolated-subprocess/v1",
        "source_import_mode": "verified_frozen_source_root_only",
        "timeout_seconds": CHILD_TIMEOUT_SECONDS,
    }


def build_factor_v3_parent_source_candidate_run_spec(
    *,
    frozen_source_root: str | Path,
    python_executable: str | Path,
    parent_materialization_manifest_path: str | Path,
    overlay_manifest_path: str | Path,
    suspension_metadata_path: str | Path,
    run_root: str | Path,
    verification_root: str | Path,
    external_run_claim_path: str | Path,
    external_verification_claim_path: str | Path,
    runtime_dependency_manifest_path: str | Path,
    expected_runtime_dependency_manifest_file_sha256: str,
) -> dict[str, Any]:
    """Build the candidate replay contract without opening any research artifact."""

    source_root = Path(frozen_source_root)
    python = Path(python_executable)
    parent_manifest = Path(parent_materialization_manifest_path)
    overlay_manifest = Path(overlay_manifest_path)
    suspension = Path(suspension_metadata_path)
    run = Path(run_root)
    verification = Path(verification_root)
    external_run_claim = Path(external_run_claim_path)
    external_verification_claim = Path(external_verification_claim_path)
    runtime_manifest_path = Path(runtime_dependency_manifest_path)
    paths = (
        source_root,
        python,
        parent_manifest,
        overlay_manifest,
        suspension,
        run,
        verification,
        external_run_claim,
        external_verification_claim,
        runtime_manifest_path,
    )
    if not all(path.is_absolute() for path in paths):
        raise FactorV3ParentSourceAuthorityRunnerError("run spec paths must be absolute")
    if python.resolve(strict=True) != PINNED_PYTHON_EXECUTABLE:
        raise FactorV3ParentSourceAuthorityRunnerError("python executable is not the pinned runner runtime")
    runtime_manifest, runtime_raw = _read_json(
        runtime_manifest_path,
        label="runtime dependency manifest",
        max_bytes=_MAX_RUNTIME_MANIFEST_BYTES,
    )
    expected_runtime_file_sha = _strict_sha256(
        expected_runtime_dependency_manifest_file_sha256,
        label="runtime dependency manifest file",
    )
    if hashlib.sha256(runtime_raw).hexdigest() != expected_runtime_file_sha:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest file content address rejected"
        )
    runtime_manifest = _validated_runtime_dependency_manifest(runtime_manifest)
    canonical_run = run.resolve(strict=False)
    canonical_verification = verification.resolve(strict=False)
    canonical_external_run_claim = external_run_claim.resolve(strict=False)
    canonical_external_verification_claim = external_verification_claim.resolve(strict=False)
    _assert_output_paths_disjoint(
        run_root=canonical_run,
        verification_root=canonical_verification,
        external_run_claim=canonical_external_run_claim,
        external_verification_claim=canonical_external_verification_claim,
        input_paths=(
            source_root,
            parent_manifest.parent,
            overlay_manifest.parent,
            suspension.parent,
            runtime_manifest_path,
            Path(runtime_manifest["site_packages_root"]),
        ),
    )
    if (
        canonical_run == canonical_verification
        or canonical_external_run_claim == canonical_external_verification_claim
        or canonical_external_run_claim.parent == canonical_run
        or canonical_external_verification_claim.parent == canonical_verification
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "run and verification ownership paths rejected"
        )
    unsigned = {
        "development_only": True,
        "external_claim_path_sha256": hashlib.sha256(
            str(canonical_external_run_claim).encode("utf-8")
        ).hexdigest(),
        "external_run_claim_path": str(canonical_external_run_claim),
        "external_verification_claim_path": str(canonical_external_verification_claim),
        "formal_materialization_eligible": False,
        "frozen_source": {
            "checkout_policy": CHECKOUT_POLICY,
            "expected_blob_sha256": dict(FROZEN_SOURCE_BLOB_SHA256),
            "expected_commit": FROZEN_SOURCE_COMMIT,
            "expected_tree_oid": FROZEN_SOURCE_TREE_OID,
            "root": str(source_root),
        },
        "overlay": {
            "expected_artifact_sha256": OVERLAY_ARTIFACT_SHA256,
            "expected_factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
            "expected_manifest_file_sha256": OVERLAY_MANIFEST_FILE_SHA256,
            "manifest_path": str(overlay_manifest),
        },
        "parent": {
            "expected_artifact_sha256": PARENT_ARTIFACT_SHA256,
            "expected_manifest_file_sha256": PARENT_MANIFEST_FILE_SHA256,
            "manifest_path": str(parent_manifest),
        },
        "run_root": str(canonical_run),
        "run_root_sha256": hashlib.sha256(str(canonical_run).encode("utf-8")).hexdigest(),
        "runtime_dependencies": {
            "allowed_import_network_event_sequences": runtime_manifest[
                "allowed_import_network_event_sequences"
            ],
            "manifest_file_sha256": expected_runtime_file_sha,
            "manifest_path": str(runtime_manifest_path),
            "manifest_root_sha256": runtime_manifest["manifest_root_sha256"],
            "required_modules": list(_REQUIRED_RUNTIME_MODULES),
            "site_packages_root": runtime_manifest["site_packages_root"],
        },
        "schema": RUN_SPEC_SCHEMA,
        "subprocess": _subprocess_contract(python),
        "suspension": {
            "expected_bundle_sha256": SUSPENSION_BUNDLE_SHA256,
            "expected_metadata_sha256": SUSPENSION_METADATA_SHA256,
            "metadata_path": str(suspension),
        },
        "verification_root": str(canonical_verification),
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "run_spec_sha256": _canonical_sha256(unsigned)}


def _validated_run_spec(value: Any) -> dict[str, Any]:
    spec = _assert_fields(value, _RUN_SPEC_FIELDS, label="run spec")
    if spec.get("schema") != RUN_SPEC_SCHEMA:
        raise FactorV3ParentSourceAuthorityRunnerError("run spec schema rejected")
    _scope_false(spec, label="run spec")
    frozen = _assert_fields(spec["frozen_source"], _FROZEN_FIELDS, label="frozen source")
    parent = _assert_fields(spec["parent"], _PARENT_FIELDS, label="parent")
    overlay = _assert_fields(spec["overlay"], _OVERLAY_FIELDS, label="overlay")
    suspension = _assert_fields(spec["suspension"], _SUSPENSION_FIELDS, label="suspension")
    runtime = _assert_fields(
        spec["runtime_dependencies"],
        _RUNTIME_DEPENDENCY_FIELDS,
        label="runtime dependencies",
    )
    child = _assert_fields(spec["subprocess"], _SUBPROCESS_FIELDS, label="subprocess")
    source_root = _absolute_path(frozen["root"], label="frozen source")
    python = _absolute_path(child["python_executable"], label="python executable")
    git = _absolute_path(child["git_executable"], label="Git executable")
    parent_manifest = _absolute_path(parent["manifest_path"], label="parent manifest")
    overlay_manifest = _absolute_path(overlay["manifest_path"], label="overlay manifest")
    suspension_path = _absolute_path(suspension["metadata_path"], label="suspension metadata")
    run_root = _absolute_path(spec["run_root"], label="run root")
    verification_root = _absolute_path(spec["verification_root"], label="verification root")
    external_run_claim = _absolute_path(
        spec["external_run_claim_path"],
        label="external run claim",
    )
    external_verification_claim = _absolute_path(
        spec["external_verification_claim_path"],
        label="external verification claim",
    )
    runtime_manifest_path = _absolute_path(
        runtime["manifest_path"],
        label="runtime dependency manifest",
    )
    runtime_root = _absolute_path(
        runtime["site_packages_root"],
        label="runtime dependency root",
    )
    _assert_output_paths_disjoint(
        run_root=run_root,
        verification_root=verification_root,
        external_run_claim=external_run_claim,
        external_verification_claim=external_verification_claim,
        input_paths=(
            source_root,
            parent_manifest.parent,
            overlay_manifest.parent,
            suspension_path.parent,
            runtime_manifest_path,
            runtime_root,
        ),
    )
    if frozen.get("expected_commit") != FROZEN_SOURCE_COMMIT:
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source commit rejected")
    if frozen.get("expected_tree_oid") != FROZEN_SOURCE_TREE_OID:
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source tree rejected")
    if frozen.get("checkout_policy") != CHECKOUT_POLICY:
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source checkout policy rejected")
    blobs = frozen.get("expected_blob_sha256")
    if type(blobs) is not dict or blobs != FROZEN_SOURCE_BLOB_SHA256:
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source blob contract rejected")
    for path, digest in blobs.items():
        if type(path) is not str or not path.startswith("app/") or "\\" in path:
            raise FactorV3ParentSourceAuthorityRunnerError("frozen source blob path rejected")
        _strict_sha256(digest, label="frozen source blob")
    if (
        parent.get("expected_artifact_sha256") != PARENT_ARTIFACT_SHA256
        or parent.get("expected_manifest_file_sha256") != PARENT_MANIFEST_FILE_SHA256
        or parent_manifest.name != f"{PARENT_ARTIFACT_SHA256}.json"
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("parent manifest path contract rejected")
    if (
        overlay.get("expected_artifact_sha256") != OVERLAY_ARTIFACT_SHA256
        or overlay.get("expected_manifest_file_sha256") != OVERLAY_MANIFEST_FILE_SHA256
        or overlay.get("expected_factor_v2_spec_sha256") != FACTOR_V2_SPEC_SHA256
        or overlay_manifest.name != f"{OVERLAY_ARTIFACT_SHA256}.json"
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("overlay manifest path contract rejected")
    if (
        suspension.get("expected_bundle_sha256") != SUSPENSION_BUNDLE_SHA256
        or suspension.get("expected_metadata_sha256") != SUSPENSION_METADATA_SHA256
        or suspension_path.name != "metadata.sqlite3"
        or suspension_path.parent.name != SUSPENSION_BUNDLE_SHA256
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("suspension path contract rejected")
    if (
        run_root != run_root.resolve(strict=False)
        or verification_root != verification_root.resolve(strict=False)
        or external_run_claim != external_run_claim.resolve(strict=False)
        or external_verification_claim != external_verification_claim.resolve(strict=False)
        or run_root == verification_root
        or external_run_claim == external_verification_claim
        or external_run_claim.parent == run_root
        or external_verification_claim.parent == verification_root
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "run and verification ownership paths rejected"
        )
    if (
        spec.get("run_root_sha256")
        != hashlib.sha256(spec["run_root"].encode("utf-8")).hexdigest()
        or spec.get("external_claim_path_sha256")
        != hashlib.sha256(spec["external_run_claim_path"].encode("utf-8")).hexdigest()
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("run ownership binding rejected")
    runtime_value, runtime_raw = _read_json(
        runtime_manifest_path,
        label="runtime dependency manifest",
        max_bytes=_MAX_RUNTIME_MANIFEST_BYTES,
    )
    runtime_manifest = _validated_runtime_dependency_manifest(runtime_value)
    if (
        hashlib.sha256(runtime_raw).hexdigest()
        != _strict_sha256(
            runtime["manifest_file_sha256"],
            label="runtime dependency manifest file",
        )
        or runtime.get("manifest_root_sha256")
        != runtime_manifest["manifest_root_sha256"]
        or runtime.get("site_packages_root")
        != runtime_manifest["site_packages_root"]
        or runtime_root != Path(runtime_manifest["site_packages_root"])
        or runtime.get("required_modules") != list(_REQUIRED_RUNTIME_MODULES)
        or runtime.get("allowed_import_network_event_sequences")
        != runtime_manifest["allowed_import_network_event_sequences"]
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "runtime dependency manifest binding rejected"
        )
    if (
        python.resolve(strict=True) != PINNED_PYTHON_EXECUTABLE
        or git.resolve(strict=True) != PINNED_GIT_EXECUTABLE
        or child != _subprocess_contract(python)
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("subprocess contract rejected")
    _strict_oid(frozen["expected_commit"], label="frozen source commit")
    _strict_oid(frozen["expected_tree_oid"], label="frozen source tree")
    for field in ("expected_artifact_sha256", "expected_manifest_file_sha256"):
        _strict_sha256(parent[field], label=f"parent {field}")
        _strict_sha256(overlay[field], label=f"overlay {field}")
    _strict_sha256(overlay["expected_factor_v2_spec_sha256"], label="factor-v2 spec")
    _strict_sha256(suspension["expected_bundle_sha256"], label="suspension bundle")
    _strict_sha256(suspension["expected_metadata_sha256"], label="suspension metadata")
    expected_self = _strict_sha256(spec["run_spec_sha256"], label="run spec")
    unsigned = dict(spec)
    unsigned.pop("run_spec_sha256")
    if expected_self != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceAuthorityRunnerError("run spec self hash rejected")
    del source_root
    return json.loads(_canonical_bytes(spec))


def _is_reparse_point(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.is_symlink():
        return True
    try:
        metadata = candidate.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_safe_path(path: Path, *, label: str, regular: bool | None) -> None:
    if not path.exists():
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} unavailable")
    current = path
    while True:
        if _is_reparse_point(current):
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} reparse point rejected")
        if current == current.parent:
            break
        current = current.parent
    if regular is True and not path.is_file():
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} regular file rejected")
    if regular is False and not path.is_dir():
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} directory rejected")


def _assert_frozen_source_cache_free(source_root: Path) -> set[str]:
    app_root = source_root / "app"
    _assert_safe_path(app_root, label="frozen app root", regular=False)
    pending = [app_root]
    python_sources: set[str] = set()
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source bytecode cache scan rejected"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_reparse_point(path):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source bytecode cache reparse rejected"
                )
            if entry.is_dir(follow_symlinks=False):
                if entry.name == "__pycache__":
                    continue
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                if path.suffix.lower() in {".pyc", ".pyo"}:
                    continue
                if path.suffix.lower() == ".py":
                    python_sources.add(path.relative_to(source_root).as_posix())
            else:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source special path rejected"
                )
    return python_sources


def _read_file(path: Path, *, label: str, max_bytes: int | None = None) -> tuple[bytes, os.stat_result]:
    _assert_safe_path(path, label=label, regular=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} open rejected") from exc
    chunks: list[bytes] = []
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (max_bytes is not None and before.st_size > max_bytes):
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} size rejected")
        while True:
            chunk = os.read(descriptor, _CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
            if max_bytes is not None and sum(map(len, chunks)) > max_bytes:
                raise FactorV3ParentSourceAuthorityRunnerError(f"{label} size rejected")
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} changed while read")
    finally:
        os.close(descriptor)
    _assert_safe_path(path, label=label, regular=True)
    current = path.stat()
    if not stat.S_ISREG(current.st_mode):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} path regular file rejected")
    if _stat_identity(current) != _stat_identity(before):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} path changed while read")
    return b"".join(chunks), before


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _read_json(path: Path, *, label: str, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    raw, _metadata = _read_file(path, label=label, max_bytes=max_bytes)
    return _strict_json(raw, label=label, max_bytes=max_bytes), raw


def load_factor_v3_parent_source_candidate_run_spec(
    path_value: str | Path,
    *,
    expected_file_sha256: str | None = None,
) -> dict[str, Any]:
    path = Path(path_value)
    if not path.is_absolute():
        raise FactorV3ParentSourceAuthorityRunnerError("run spec path must be absolute")
    value, raw = _read_json(path, label="run spec", max_bytes=_MAX_RUN_SPEC_BYTES)
    observed = hashlib.sha256(raw).hexdigest()
    if expected_file_sha256 is not None and observed != _strict_sha256(
        expected_file_sha256,
        label="run spec file",
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("run spec file content address rejected")
    return _validated_run_spec(value)


_GIT_LOCAL_CONFIG_ALLOWLIST = {
    "core": {
        "bare",
        "filemode",
        "ignorecase",
        "logallrefupdates",
        "precomposeunicode",
        "repositoryformatversion",
        "symlinks",
    },
    "remote": {"fetch", "url"},
    "branch": {"description", "merge", "remote"},
}


def _validate_git_local_config(raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "frozen source Git local config encoding rejected"
        ) from exc
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            category = header.split(maxsplit=1)[0].casefold()
            if category not in _GIT_LOCAL_CONFIG_ALLOWLIST:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source Git local config section rejected"
                )
            section = category
            continue
        if section is None:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git local config syntax rejected"
            )
        key_text = line.split("=", 1)[0].strip()
        key = key_text.split(maxsplit=1)[0].casefold()
        if key not in _GIT_LOCAL_CONFIG_ALLOWLIST[section]:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git local config key rejected"
            )


class _GitCommandBoundary:
    def __init__(self, root: Path) -> None:
        self.handles: list[Any] = []
        self.root = root
        try:
            dot_git = root / ".git"
            _assert_safe_path(dot_git, label="frozen source Git marker", regular=None)
            if dot_git.is_file():
                marker = _HeldRunEvidenceFile(dot_git, label="frozen source Git marker")
                self.handles.append(marker)
                try:
                    marker_text = marker.raw.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise FactorV3ParentSourceAuthorityRunnerError(
                        "frozen source Git marker encoding rejected"
                    ) from exc
                if not marker_text.casefold().startswith("gitdir:"):
                    raise FactorV3ParentSourceAuthorityRunnerError(
                        "frozen source Git marker rejected"
                    )
                git_dir_value = marker_text.split(":", 1)[1].strip()
                git_dir = Path(git_dir_value)
                if not git_dir.is_absolute():
                    git_dir = root / git_dir
            elif dot_git.is_dir():
                git_dir = dot_git
            else:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source Git marker type rejected"
                )
            self.git_dir = git_dir.resolve(strict=True)
            _assert_safe_path(
                self.git_dir,
                label="frozen source Git directory",
                regular=False,
            )
            common_marker_path = self.git_dir / "commondir"
            if common_marker_path.exists():
                common_marker = _HeldRunEvidenceFile(
                    common_marker_path,
                    label="frozen source Git common marker",
                )
                self.handles.append(common_marker)
                try:
                    common_value = common_marker.raw.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise FactorV3ParentSourceAuthorityRunnerError(
                        "frozen source Git common marker encoding rejected"
                    ) from exc
                common_dir = Path(common_value)
                if not common_dir.is_absolute():
                    common_dir = self.git_dir / common_dir
                self.common_dir = common_dir.resolve(strict=True)
            else:
                self.common_dir = self.git_dir
            _assert_safe_path(
                self.common_dir,
                label="frozen source Git common directory",
                regular=False,
            )
            config = _HeldRunEvidenceFile(
                self.common_dir / "config",
                label="frozen source Git local config",
            )
            self.handles.append(config)
            _validate_git_local_config(config.raw)
            if (self.git_dir / "config.worktree").exists():
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source Git worktree config rejected"
                )
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self) -> None:
        for handle in self.handles:
            handle.verify()
        if (self.git_dir / "config.worktree").exists():
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git worktree config changed"
            )

    def close(self) -> None:
        while self.handles:
            self.handles.pop().close()


def _git_command(root: Path, *arguments: str) -> bytes:
    boundary = _GitCommandBoundary(root)
    environment = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_KEY_2": "core.attributesFile",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_VALUE_2": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            environment[key] = os.environ[key]
    job = None
    process: Any = None
    error: BaseException | None = None
    exit_code: int | None = None
    with tempfile.TemporaryFile("w+b") as stdout_handle, tempfile.TemporaryFile(
        "w+b"
    ) as stderr_handle:
        try:
            kwargs: dict[str, Any] = {
                "close_fds": True,
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": stdout_handle,
                "stderr": stderr_handle,
                "shell": False,
            }
            if os.name == "nt":
                kwargs["creationflags"] = _CREATE_SUSPENDED
            process = subprocess.Popen(
                [
                    str(PINNED_GIT_EXECUTABLE),
                    "--no-replace-objects",
                    "--no-optional-locks",
                    f"--git-dir={boundary.git_dir}",
                    f"--work-tree={root}",
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    f"core.attributesFile={os.devnull}",
                    *arguments,
                ],
                **kwargs,
            )
            job = _assign_windows_job(process)
            _resume_suspended_process(process)
            try:
                process.communicate(timeout=60)
            except subprocess.TimeoutExpired as exc:
                if job is not None:
                    _terminate_windows_job(job)
                else:
                    process.kill()
                process.communicate()
                error = FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source Git timeout rejected"
                )
                error.__cause__ = exc
            exit_code = process.returncode
        except OSError as exc:
            error = FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git unavailable"
            )
            error.__cause__ = exc
        except BaseException as exc:
            error = exc
        finally:
            if process is not None and error is not None and process.returncode is None:
                try:
                    if job is not None:
                        _terminate_windows_job(job)
                    else:
                        exit_code = _kill_and_wait_process(process, timeout_seconds=60)
                except BaseException as exc:
                    error = exc
            if job is not None:
                try:
                    _wait_for_windows_job_tree(job, timeout_seconds=60)
                    if process is not None and process.returncode is None:
                        exit_code = process.wait(timeout=60)
                except BaseException as exc:
                    error = exc
                finally:
                    _close_windows_job(job)
                if process is not None and process.returncode is None:
                    try:
                        exit_code = process.wait(timeout=60)
                    except BaseException as exc:
                        error = exc
            if process is not None and exit_code is None:
                exit_code = process.returncode
            try:
                boundary.verify()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            boundary.close()
            if isinstance(error, FactorV3ParentSourceAuthorityRunnerError):
                raise error
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git verification rejected"
            ) from error
        stdout_handle.seek(0)
        stdout = stdout_handle.read(16 * 1024 * 1024 + 1)
        stderr_handle.seek(0)
        stderr = stderr_handle.read(1024 * 1024 + 1)
    if exit_code != 0 or stderr or len(stdout) > 16 * 1024 * 1024:
        boundary.close()
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source Git verification rejected")
    boundary.close()
    return stdout


def _git_text(root: Path, *arguments: str) -> str:
    try:
        return _git_command(root, *arguments).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError("frozen source Git output rejected") from exc


def _git_app_python_index_matches_commit(root: Path) -> bool:
    tree_entries: dict[str, tuple[str, str]] = {}
    raw_tree = _git_command(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        FROZEN_SOURCE_COMMIT,
        "--",
        "app",
    )
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git tree entry rejected"
            ) from exc
        if object_type == "blob" and relative.endswith(".py"):
            if relative in tree_entries:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source Git tree duplicate rejected"
                )
            tree_entries[relative] = (mode, oid)

    index_entries: dict[str, tuple[str, str]] = {}
    raw_index = _git_command(root, "ls-files", "-s", "-z", "--", "app")
    for record in raw_index.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git index entry rejected"
            ) from exc
        if relative.endswith(".py"):
            if stage != "0" or relative in index_entries:
                return False
            index_entries[relative] = (mode, oid)
    return bool(tree_entries) and index_entries == tree_entries


def _git_repository_controls(root: Path) -> dict[str, Any]:
    raw_common = _git_text(root, "rev-parse", "--git-common-dir")
    common = Path(raw_common)
    if not common.is_absolute():
        common = root / common
    common = common.resolve(strict=True)
    _assert_safe_path(common, label="frozen source Git common directory", regular=False)
    alternates = common / "objects" / "info" / "alternates"
    if os.path.lexists(alternates):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "frozen source Git object alternates rejected"
        )
    replace_root = common / "refs" / "replace"
    if os.path.lexists(replace_root):
        _assert_safe_path(
            replace_root,
            label="frozen source Git replacement refs",
            regular=False,
        )
        if any(replace_root.rglob("*")):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git replacement refs rejected"
            )
    packed_refs = common / "packed-refs"
    if packed_refs.exists():
        raw, _metadata = _read_file(
            packed_refs,
            label="frozen source packed refs",
            max_bytes=16 * 1024 * 1024,
        )
        if b" refs/replace/" in raw:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source packed replacement refs rejected"
            )
    return {
        "common_directory_sha256": hashlib.sha256(
            str(common).encode("utf-8")
        ).hexdigest(),
        "object_alternates_absent": True,
        "replacement_refs_absent": True,
    }


def _git_identity(root: Path) -> dict[str, Any]:
    top_level = _git_text(root, "rev-parse", "--show-toplevel")
    flags = _git_text(root, "ls-files", "-v", "--", "app")
    index_clean = _git_app_python_index_matches_commit(root)
    flags_clean = bool(flags) and all(line.startswith("H ") for line in flags.splitlines())
    relevant_clean = index_clean and flags_clean
    return {
        "clean": relevant_clean,
        "commit": _git_text(root, "rev-parse", "HEAD"),
        "repository_controls": _git_repository_controls(root),
        "index_diff_clean": index_clean,
        "index_flags_clean": flags_clean,
        "inside_work_tree": _git_text(root, "rev-parse", "--is-inside-work-tree") == "true",
        "top_level_sha256": hashlib.sha256(
            str(Path(top_level).resolve(strict=True)).encode("utf-8")
        ).hexdigest(),
        "tree_oid": _git_text(root, "rev-parse", "HEAD^{tree}"),
        "worktree_diff_clean": relevant_clean,
    }


def _git_blob_bytes(root: Path, relative: str) -> bytes:
    return _git_command(root, "cat-file", "blob", f"{FROZEN_SOURCE_COMMIT}:{relative}")


def _tracked_app_source_blobs(root: Path) -> dict[str, dict[str, str]]:
    raw = _git_command(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        FROZEN_SOURCE_COMMIT,
        "--",
        "app",
    )
    result: dict[str, dict[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_oid = header.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source tree record rejected"
            ) from exc
        if not relative.endswith(".py"):
            continue
        if (
            mode not in {"100644", "100755"}
            or object_type != "blob"
            or not relative.startswith("app/")
            or "\\" in relative
            or relative in result
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source tree Python entry rejected"
            )
        blob = _git_command(root, "cat-file", "blob", object_oid)
        if b"\r" in blob:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git blob newline policy rejected"
            )
        result[relative] = {
            "git_blob_sha256": hashlib.sha256(blob).hexdigest(),
            "physical_sha256": hashlib.sha256(blob.replace(b"\n", b"\r\n")).hexdigest(),
        }
    if not set(FROZEN_SOURCE_BLOB_SHA256) <= set(result):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "frozen source pinned blob closure incomplete"
        )
    if any(
        result[relative]["git_blob_sha256"] != expected
        for relative, expected in FROZEN_SOURCE_BLOB_SHA256.items()
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "frozen source pinned blob closure rejected"
        )
    return dict(sorted(result.items()))


def _open_strong_read(path: Path, *, label: str) -> int:
    _assert_safe_path(path, label=label, regular=True)
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(str(path), flags)
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} strong open rejected") from exc

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
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
        0x80000000,
        0x00000001,
        None,
        3,
        0x00200000 | 0x00000080,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} strong open rejected")
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} strong open rejected") from exc


class _StrongRuntimeFile:
    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> None:
        self.path = path
        self.expected_sha256 = expected_sha256
        self.expected_size_bytes = expected_size_bytes
        _assert_safe_path(path, label="runtime dependency file", regular=True)
        if os.name == "nt":
            self._open_windows()
        else:
            self.handle = _open_strong_read(path, label="runtime dependency file")
            self.identity = _stat_identity(os.fstat(self.handle))
        try:
            self.verify()
        except BaseException:
            self.close()
            raise

    def _open_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
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
        self.handle = create_file(
            str(self.path),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if self.handle in (None, invalid):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency strong handle rejected"
            )
        self.identity = self._windows_identity()

    def _windows_identity(self) -> tuple[int, ...]:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

        class FILE_INFO(ctypes.Structure):
            _fields_ = (
                ("attributes", wintypes.DWORD),
                ("creation", FILETIME),
                ("access", FILETIME),
                ("write", FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            )

        function = ctypes.windll.kernel32.GetFileInformationByHandle
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(FILE_INFO))
        function.restype = wintypes.BOOL
        info = FILE_INFO()
        if not function(self.handle, ctypes.byref(info)):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency handle identity rejected"
            )
        if info.attributes & 0x10 or info.attributes & 0x400:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency handle type rejected"
            )
        return (
            int(info.volume),
            int(info.index_high),
            int(info.index_low),
            int(info.size_high),
            int(info.size_low),
            int(info.write.high),
            int(info.write.low),
        )

    def _sha256(self) -> str:
        if os.name != "nt":
            return _hash_fd(self.handle)
        import ctypes
        from ctypes import wintypes

        set_pointer = ctypes.windll.kernel32.SetFilePointerEx
        set_pointer.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        set_pointer.restype = wintypes.BOOL
        if not set_pointer(self.handle, 0, None, 0):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency handle seek rejected"
            )
        read_file = ctypes.windll.kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        read_file.restype = wintypes.BOOL
        buffer = ctypes.create_string_buffer(_CHUNK_BYTES)
        digest = hashlib.sha256()
        while True:
            read = wintypes.DWORD()
            if not read_file(
                self.handle,
                buffer,
                len(buffer),
                ctypes.byref(read),
                None,
            ):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runtime dependency handle read rejected"
                )
            if read.value == 0:
                break
            digest.update(buffer.raw[: read.value])
        return digest.hexdigest()

    def verify(self) -> None:
        identity = (
            self._windows_identity()
            if os.name == "nt"
            else _stat_identity(os.fstat(self.handle))
        )
        size = (
            (identity[3] << 32) | identity[4]
            if os.name == "nt"
            else identity[2]
        )
        if (
            identity != self.identity
            or size != self.expected_size_bytes
            or self._sha256() != self.expected_sha256
            or _is_reparse_point(self.path)
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency held content changed"
            )

    def close(self) -> None:
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self.handle)
        else:
            os.close(self.handle)


def _hash_fd(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, _CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _read_bounded_fd(descriptor: int, *, max_bytes: int, label: str) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(_CHUNK_BYTES, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} size rejected")
    return b"".join(chunks)


def _assert_sidecars_absent(database_paths: Sequence[Path]) -> None:
    for database in database_paths:
        for suffix in _SIDECAR_SUFFIXES:
            if os.path.lexists(f"{database}{suffix}"):
                raise FactorV3ParentSourceAuthorityRunnerError("SQLite sidecar rejected")


def _input_paths(
    spec: Mapping[str, Any],
    app_sources: Mapping[str, Mapping[str, str]],
    runtime_manifest: Mapping[str, Any],
) -> list[tuple[str, Path, str | None]]:
    source_root = Path(spec["frozen_source"]["root"])
    parent_manifest = Path(spec["parent"]["manifest_path"])
    overlay_manifest = Path(spec["overlay"]["manifest_path"])
    suspension_database = Path(spec["suspension"]["metadata_path"])
    paths: list[tuple[str, Path, str | None]] = [
        (
            "python-executable",
            Path(spec["subprocess"]["python_executable"]),
            spec["subprocess"]["python_executable_sha256"],
        ),
        (
            "git-executable",
            Path(spec["subprocess"]["git_executable"]),
            spec["subprocess"]["git_executable_sha256"],
        ),
        (
            "parent-manifest",
            parent_manifest,
            spec["parent"]["expected_manifest_file_sha256"],
        ),
        (
            "overlay-manifest",
            overlay_manifest,
            spec["overlay"]["expected_manifest_file_sha256"],
        ),
        (
            "suspension-manifest",
            suspension_database.parent / "manifest.json",
            None,
        ),
        (
            "suspension-metadata",
            suspension_database,
            spec["suspension"]["expected_metadata_sha256"],
        ),
    ]
    paths.extend(
        (f"parent-database:{name}", parent_manifest.parent / name, None)
        for name in PARENT_DATABASE_FILENAMES
    )
    paths.append(
        (
            "overlay-database",
            overlay_manifest.parent / OVERLAY_DATABASE_FILENAME,
            None,
        )
    )
    paths.extend(
        (
            f"frozen-source:{relative}",
            source_root / Path(*relative.split("/")),
            binding["git_blob_sha256"],
        )
        for relative, binding in sorted(app_sources.items())
    )
    return sorted(paths, key=lambda item: item[0])


def _database_paths(spec: Mapping[str, Any]) -> list[Path]:
    parent_manifest = Path(spec["parent"]["manifest_path"])
    overlay_manifest = Path(spec["overlay"]["manifest_path"])
    suspension = Path(spec["suspension"]["metadata_path"])
    return [
        *(parent_manifest.parent / name for name in PARENT_DATABASE_FILENAMES),
        overlay_manifest.parent / OVERLAY_DATABASE_FILENAME,
        suspension,
    ]


class _InputClosure:
    def __init__(self, spec: Mapping[str, Any]) -> None:
        self.spec = spec
        self.source_root = Path(spec["frozen_source"]["root"])
        self.databases = _database_paths(spec)
        self.runtime_root = Path(spec["runtime_dependencies"]["site_packages_root"])
        self.handles: list[tuple[str, Path, str | None, int, os.stat_result]] = []
        self.runtime_handles: list[_StrongRuntimeFile] = []
        _assert_safe_path(self.source_root, label="frozen source root", regular=False)
        try:
            for label, path, expected in (
                (
                    "python-executable",
                    Path(spec["subprocess"]["python_executable"]),
                    spec["subprocess"]["python_executable_sha256"],
                ),
                (
                    "git-executable",
                    Path(spec["subprocess"]["git_executable"]),
                    spec["subprocess"]["git_executable_sha256"],
                ),
                (
                    "runtime-dependency-manifest",
                    Path(spec["runtime_dependencies"]["manifest_path"]),
                    spec["runtime_dependencies"]["manifest_file_sha256"],
                ),
            ):
                descriptor = _open_strong_read(path, label=label)
                metadata = os.fstat(descriptor)
                self.handles.append((label, path, expected, descriptor, metadata))
                self._entry(self.handles[-1])
            manifest_handle = next(
                item for item in self.handles if item[0] == "runtime-dependency-manifest"
            )
            runtime_raw = _read_bounded_fd(
                manifest_handle[3],
                max_bytes=_MAX_RUNTIME_MANIFEST_BYTES,
                label="runtime dependency manifest",
            )
            self.runtime_manifest = _validated_runtime_dependency_manifest(
                _strict_json(
                    runtime_raw,
                    label="runtime dependency manifest",
                    max_bytes=_MAX_RUNTIME_MANIFEST_BYTES,
                )
            )
            if (
                self.runtime_manifest["manifest_root_sha256"]
                != spec["runtime_dependencies"]["manifest_root_sha256"]
                or _runtime_dependency_files(self.runtime_root)
                != self.runtime_manifest["files"]
            ):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "runtime dependency exact tree rejected"
                )
            for item in self.runtime_manifest["files"]:
                self.runtime_handles.append(
                    _StrongRuntimeFile(
                        self.runtime_root
                        / Path(*item["relative_path"].split("/")),
                        expected_sha256=item["sha256"],
                        expected_size_bytes=item["size_bytes"],
                    )
                )
            self.app_sources = _tracked_app_source_blobs(self.source_root)
            if _assert_frozen_source_cache_free(self.source_root) != set(self.app_sources):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "frozen source tracked Python closure rejected"
                )
            _assert_sidecars_absent(self.databases)
            for label, path, expected in _input_paths(
                spec,
                self.app_sources,
                self.runtime_manifest,
            ):
                if label in {
                    "python-executable",
                    "git-executable",
                    "runtime-dependency-manifest",
                }:
                    continue
                descriptor = _open_strong_read(path, label=label)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    os.close(descriptor)
                    raise FactorV3ParentSourceAuthorityRunnerError(
                        f"{label} strong regular file rejected"
                    )
                self.handles.append((label, path, expected, descriptor, metadata))
        except BaseException:
            self.close()
            raise

    def _source_entry(
        self,
        *,
        label: str,
        path: Path,
        expected: str,
        descriptor: int,
        metadata: os.stat_result,
    ) -> dict[str, Any]:
        relative = label.removeprefix("frozen-source:")
        physical = _read_bounded_fd(descriptor, max_bytes=16 * 1024 * 1024, label=label)
        binding = self.app_sources.get(relative)
        if (
            binding is None
            or binding["git_blob_sha256"] != expected
            or binding["physical_sha256"] != hashlib.sha256(physical).hexdigest()
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source blob checkout attestation rejected"
            )
        return {
            "checkout_policy": CHECKOUT_POLICY,
            "device": metadata.st_dev,
            "git_blob_sha256": expected,
            "inode": metadata.st_ino,
            "label": label,
            "mtime_ns": metadata.st_mtime_ns,
            "path_sha256": hashlib.sha256(
                str(path.resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "physical_sha256": hashlib.sha256(physical).hexdigest(),
            "size_bytes": metadata.st_size,
        }

    def _entry(
        self,
        item: tuple[str, Path, str | None, int, os.stat_result],
    ) -> dict[str, Any]:
        label, path, expected, descriptor, opened = item
        before = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(opened):
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} held input changed")
        if label.startswith("frozen-source:"):
            if expected is None:
                raise FactorV3ParentSourceAuthorityRunnerError("frozen source hash unavailable")
            result = self._source_entry(
                label=label,
                path=path,
                expected=expected,
                descriptor=descriptor,
                metadata=before,
            )
        else:
            observed = _hash_fd(descriptor)
            if expected is not None and observed != expected:
                raise FactorV3ParentSourceAuthorityRunnerError(f"{label} blob attestation rejected")
            result = {
                "device": before.st_dev,
                "inode": before.st_ino,
                "label": label,
                "mtime_ns": before.st_mtime_ns,
                "path_sha256": hashlib.sha256(
                    str(path.resolve(strict=True)).encode("utf-8")
                ).hexdigest(),
                "sha256": observed,
                "size_bytes": before.st_size,
            }
        after = os.fstat(descriptor)
        current = path.stat()
        if (
            not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(f"{label} held path changed")
        return result

    def _snapshot(self) -> dict[str, Any]:
        if _assert_frozen_source_cache_free(self.source_root) != set(self.app_sources):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source tracked Python closure changed"
            )
        if _runtime_dependency_files(self.runtime_root) != self.runtime_manifest["files"]:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "runtime dependency exact tree changed"
            )
        identity = _git_identity(self.source_root)
        expected_identity = {
            "clean": True,
            "commit": FROZEN_SOURCE_COMMIT,
            "index_diff_clean": True,
            "index_flags_clean": True,
            "inside_work_tree": True,
            "repository_controls": identity["repository_controls"],
            "top_level_sha256": hashlib.sha256(
                str(self.source_root.resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "tree_oid": FROZEN_SOURCE_TREE_OID,
            "worktree_diff_clean": True,
        }
        if identity != expected_identity:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "frozen source Git attestation rejected"
            )
        _assert_sidecars_absent(self.databases)
        entries = [
            self._entry(item)
            for item in sorted(self.handles, key=lambda current: current[0])
        ]
        for handle in self.runtime_handles:
            handle.verify()
        _assert_sidecars_absent(self.databases)
        unsigned = {
            "files": entries,
            "frozen_source": identity,
            "schema": "factor-v3-parent-source-read-only-attestation/v1",
            "sidecars_absent": True,
            "source_bytecode_cache_bypassed": True,
            "tracked_app_source_count": len(self.app_sources),
            "tracked_app_sources_sha256": _canonical_sha256(self.app_sources),
            "runtime_dependency_file_count": self.runtime_manifest["file_count"],
            "runtime_dependency_manifest_root_sha256": self.runtime_manifest[
                "manifest_root_sha256"
            ],
        }
        return {**unsigned, "attestation_sha256": _canonical_sha256(unsigned)}

    def preflight(self) -> dict[str, Any]:
        return self._snapshot()

    def postflight(self) -> dict[str, Any]:
        return self._snapshot()

    def close(self) -> None:
        while self.runtime_handles:
            self.runtime_handles.pop().close()
        while self.handles:
            _label, _path, _expected, descriptor, _metadata = self.handles.pop()
            try:
                os.close(descriptor)
            except OSError:
                pass


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _create_only(path: Path, raw: bytes, *, label: str) -> None:
    parent_chain = _HeldDirectoryChain(path.parent, label=f"{label} parent")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(str(path), flags, 0o600)
        except FileExistsError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{label} already claimed or exists"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        parent_chain.verify()
        _fsync_directory(path.parent)
    finally:
        parent_chain.close()


class _HeldDirectoryChain:
    def __init__(self, path: Path, *, label: str) -> None:
        if not path.is_absolute():
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{label} directory chain must be absolute"
            )
        self.path = Path(os.path.abspath(path))
        self.label = label
        self.handles: list[tuple[Path, Any, tuple[int, ...] | tuple[int, int, int, int]]] = []
        try:
            current = Path(self.path.anchor)
            for part in self.path.parts[1:]:
                current /= part
                self.handles.append((current, *self._open(current)))
            if not self.handles:
                self.handles.append((self.path, *self._open(self.path)))
            self.verify()
        except BaseException:
            self.close()
            raise

    def _open(self, path: Path) -> tuple[Any, tuple[int, ...] | tuple[int, int, int, int]]:
        _assert_safe_path(path, label=self.label, regular=False)
        if os.name != "nt":
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(str(path), flags)
            metadata = os.fstat(descriptor)
            return descriptor, (metadata.st_dev, metadata.st_ino)

        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
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
            0x00000080,
            0x00000001 | 0x00000002,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} directory strong open rejected"
            )
        try:
            return handle, self._windows_identity(handle)
        except BaseException:
            ctypes.windll.kernel32.CloseHandle(handle)
            raise

    def _windows_identity(self, handle: Any) -> tuple[int, ...]:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

        class FILE_INFO(ctypes.Structure):
            _fields_ = (
                ("attributes", wintypes.DWORD),
                ("creation", FILETIME),
                ("access", FILETIME),
                ("write", FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            )

        function = ctypes.windll.kernel32.GetFileInformationByHandle
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(FILE_INFO))
        function.restype = wintypes.BOOL
        info = FILE_INFO()
        if not function(handle, ctypes.byref(info)):
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} directory identity rejected"
            )
        if not info.attributes & 0x10 or info.attributes & 0x400:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} directory type rejected"
            )
        return (
            int(info.volume),
            int(info.index_high),
            int(info.index_low),
        )

    def verify(self) -> None:
        for path, handle, identity in self.handles:
            if os.name == "nt":
                current_identity = self._windows_identity(handle)
            else:
                metadata = os.fstat(handle)
                current_identity = (metadata.st_dev, metadata.st_ino)
            if (
                current_identity != identity
                or _is_reparse_point(path)
                or path.resolve(strict=True) != path
                or not path.is_dir()
            ):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    f"{self.label} directory chain changed"
                )

    def close(self) -> None:
        while self.handles:
            _path, handle, _identity = self.handles.pop()
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(handle)
            else:
                os.close(handle)


def _exclusive_create_handle(path: Path, *, label: str) -> Any:
    _assert_safe_path(path.parent, label=f"{label} parent", regular=False)
    if os.name != "nt":
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(str(path), flags, 0o600)
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{label} exclusive create rejected"
            ) from exc
        return os.fdopen(descriptor, "w+b", buffering=0)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
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
        0x80000000 | 0x40000000,
        0,
        None,
        1,
        0x00200000 | 0x00000080,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise FactorV3ParentSourceAuthorityRunnerError(
            f"{label} exclusive create rejected"
        )
    try:
        descriptor = msvcrt.open_osfhandle(
            handle,
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise FactorV3ParentSourceAuthorityRunnerError(
            f"{label} exclusive create rejected"
        ) from exc
    return os.fdopen(descriptor, "w+b", buffering=0)


class _OwnedFile:
    def __init__(self, path: Path, *, label: str, raw: bytes) -> None:
        self.path = path
        self.label = label
        self.parent_chain = _HeldDirectoryChain(path.parent, label=f"{label} parent")
        try:
            self.handle = _exclusive_create_handle(path, label=label)
            self.expected_raw = b""
            self._rewrite(raw)
        except BaseException:
            self.parent_chain.close()
            raise

    def _rewrite(self, raw: bytes) -> None:
        self.handle.seek(0)
        self.handle.truncate(0)
        self.handle.write(raw)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.expected_raw = raw
        self.identity = _stat_identity(os.fstat(self.handle.fileno()))
        _fsync_directory(self.path.parent)

    def rewrite_terminal(self, raw: bytes) -> None:
        self.verify()
        self._rewrite(raw)

    def capture(self, *, max_bytes: int) -> bytes:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.seek(0)
        raw = self.handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} size rejected"
            )
        self.expected_raw = raw
        self.identity = _stat_identity(os.fstat(self.handle.fileno()))
        self.verify()
        return raw

    def verify(self) -> None:
        self.parent_chain.verify()
        self.handle.flush()
        os.fsync(self.handle.fileno())
        metadata = os.fstat(self.handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} held regular file rejected"
            )
        self.handle.seek(0)
        raw = self.handle.read(len(self.expected_raw) + 1)
        try:
            current = self.path.stat()
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} held path unavailable"
            ) from exc
        if (
            raw != self.expected_raw
            or _stat_identity(metadata) != self.identity
            or _stat_identity(current) != self.identity
            or _is_reparse_point(self.path)
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} held content changed"
            )
        self.parent_chain.verify()

    def close(self) -> None:
        try:
            self.handle.close()
        finally:
            self.parent_chain.close()


def _create_owned_cas(
    root: Path,
    category: str,
    value: Mapping[str, Any],
) -> tuple[str, str, _OwnedFile]:
    raw = _canonical_bytes(dict(value))
    file_sha256 = hashlib.sha256(raw).hexdigest()
    relative = f"{category}/sha256/{file_sha256[:2]}/{file_sha256}.json"
    target = root / Path(*relative.split("/"))
    _ensure_directory_chain(root, target.parent)
    owned = _OwnedFile(target, label=f"{category} CAS", raw=raw)
    owned.verify()
    return relative, file_sha256, owned


def _ensure_directory_chain(root: Path, target: Path) -> None:
    root = root.resolve(strict=True)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError("CAS directory escapes run root") from exc
    held: list[_HeldDirectoryChain] = []
    try:
        held.append(_HeldDirectoryChain(root, label="CAS root"))
        current = root
        for part in relative.parts:
            current = current / part
            if not current.exists():
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                _fsync_directory(current.parent)
            held.append(_HeldDirectoryChain(current, label="CAS directory"))
            for chain in held:
                chain.verify()
    finally:
        while held:
            held.pop().close()


def _create_cas(root: Path, category: str, value: Mapping[str, Any]) -> tuple[str, str]:
    raw = _canonical_bytes(dict(value))
    file_sha256 = hashlib.sha256(raw).hexdigest()
    relative = f"{category}/sha256/{file_sha256[:2]}/{file_sha256}.json"
    target = root / Path(*relative.split("/"))
    _ensure_directory_chain(root, target.parent)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.partial")
    try:
        _create_only(temporary, raw, label=f"{category} temporary")
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing, _metadata = _read_file(target, label=category, max_bytes=_MAX_STATE_BYTES)
            if existing != raw:
                raise FactorV3ParentSourceAuthorityRunnerError(f"{category} CAS collision rejected") from None
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    observed, _metadata = _read_file(target, label=category, max_bytes=_MAX_STATE_BYTES)
    if observed != raw:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{category} CAS postverification rejected")
    return relative, file_sha256


def _run_root(path_value: str | Path, *, create: bool) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        raise FactorV3ParentSourceAuthorityRunnerError("run root must be absolute")
    if not path.exists():
        if not create:
            raise FactorV3ParentSourceAuthorityRunnerError("run root unavailable")
        if not path.parent.exists():
            raise FactorV3ParentSourceAuthorityRunnerError("run root parent unavailable")
        parent_chain = _HeldDirectoryChain(path.parent, label="run root parent")
        try:
            path.mkdir()
            parent_chain.verify()
        finally:
            parent_chain.close()
    _assert_safe_path(path, label="run root", regular=False)
    return path.resolve(strict=True)


def _assert_disjoint(
    root: Path,
    spec: Mapping[str, Any],
    *,
    verification_root_is_output: bool = False,
) -> None:
    inputs = {
        Path(spec["frozen_source"]["root"]),
        Path(spec["parent"]["manifest_path"]).parent,
        Path(spec["overlay"]["manifest_path"]).parent,
        Path(spec["suspension"]["metadata_path"]).parent,
        Path(spec["runtime_dependencies"]["site_packages_root"]),
        Path(spec["runtime_dependencies"]["manifest_path"]),
        Path(spec["verification_root"]),
    }
    resolved = root.resolve(strict=True)
    for candidate in inputs:
        if verification_root_is_output and candidate == Path(spec["verification_root"]):
            continue
        target = candidate.resolve(strict=candidate.exists())
        if resolved == target or resolved.is_relative_to(target) or target.is_relative_to(resolved):
            raise FactorV3ParentSourceAuthorityRunnerError("run root overlaps read-only input")


class _RunOwnership:
    def __init__(self) -> None:
        self.handles: list[_OwnedFile] = []
        self.status: _OwnedFile | None = None

    def add(self, handle: _OwnedFile) -> _OwnedFile:
        self.handles.append(handle)
        return handle

    def verify(self) -> None:
        for handle in self.handles:
            handle.verify()

    def close(self) -> None:
        while self.handles:
            self.handles.pop().close()


def _initialize_run(
    root: Path,
    spec: Mapping[str, Any],
) -> tuple[str, _RunOwnership]:
    if root != Path(spec["run_root"]):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "canonical run root binding rejected"
        )
    if any(root.iterdir()):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "single attempt run root already claimed"
        )
    ownership = _RunOwnership()
    unsigned_claim = {
        "development_only": True,
        "external_claim_path_sha256": hashlib.sha256(
            spec["external_run_claim_path"].encode("utf-8")
        ).hexdigest(),
        "formal_materialization_eligible": False,
        "run_root_sha256": hashlib.sha256(spec["run_root"].encode("utf-8")).hexdigest(),
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": CLAIM_SCHEMA,
        "single_attempt": True,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    claim = {**unsigned_claim, "claim_sha256": _canonical_sha256(unsigned_claim)}
    claim_raw = _canonical_bytes(claim)
    reservation_unsigned = {
        "claim_sha256": claim["claim_sha256"],
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": "factor-v3-parent-source-candidate-status-reservation/v1",
        "state": "reserved",
    }
    reservation = {
        **reservation_unsigned,
        "reservation_sha256": _canonical_sha256(reservation_unsigned),
    }
    status_path = root / "status.json"
    external_claim_acquired = False
    try:
        ownership.status = ownership.add(
            _OwnedFile(
                status_path,
                label="terminal status reservation",
                raw=_canonical_bytes(reservation),
            )
        )
        ownership.add(
            _OwnedFile(
                Path(spec["external_run_claim_path"]),
                label="external single attempt claim",
                raw=claim_raw,
            )
        )
        external_claim_acquired = True
        ownership.add(
            _OwnedFile(
                root / "run-spec.json",
                label="run spec snapshot",
                raw=_canonical_bytes(spec),
            )
        )
        ownership.add(
            _OwnedFile(
                root / "claim.json",
                label="single attempt claim",
                raw=claim_raw,
            )
        )
        ownership.verify()
    except BaseException as exc:
        if external_claim_acquired and ownership.status is not None:
            try:
                _persist_failure(
                    root=root,
                    spec=spec,
                    claim_sha256=claim["claim_sha256"],
                    stage="initialize",
                    error=exc,
                    child_exit_code=None,
                    child_stdout=None,
                    child_stderr=None,
                    preflight=None,
                    postflight=None,
                    ownership=ownership,
                )
            except BaseException:
                pass
        ownership.close()
        if not external_claim_acquired:
            status_path.unlink(missing_ok=True)
        raise
    return claim["claim_sha256"], ownership


def _io_descriptor(raw: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def _status_payload(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    status: str,
    source_authority_verified: bool,
    receipt: Mapping[str, Any] | None = None,
    failure: Mapping[str, Any] | None = None,
    child_exit_code: int | None = None,
    child_stdout: Mapping[str, Any] | None = None,
    child_stderr: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise FactorV3ParentSourceAuthorityRunnerError("status transition rejected")
    unsigned = {
        "authority_status": (
            "CANDIDATE_PARENT_REPLAY_ONLY"
            if status == "completed"
            else "CANDIDATE_REPLAY_FAILED"
        ),
        "candidate_verified": status == "completed",
        "child_exit_code": child_exit_code,
        "child_stderr": None if child_stderr is None else dict(child_stderr),
        "child_stdout": None if child_stdout is None else dict(child_stdout),
        "claim_sha256": claim_sha256,
        "development_only": True,
        "failure": None if failure is None else dict(failure),
        "formal_materialization_eligible": False,
        "receipt": None if receipt is None else dict(receipt),
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": STATUS_SCHEMA,
        "source_authority_verified": source_authority_verified,
        "status": status,
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "status_sha256": _canonical_sha256(unsigned)}


def _child_input(
    spec: Mapping[str, Any],
    *,
    app_sources: Mapping[str, Mapping[str, str]],
    pycache_prefix: Path,
    runtime_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "allowed_import_network_event_sequences": spec["runtime_dependencies"][
            "allowed_import_network_event_sequences"
        ],
        "attested_app_source_physical_sha256": {
            relative: binding["physical_sha256"]
            for relative, binding in sorted(app_sources.items())
        },
        "expected_factor_v2_spec_sha256": spec["overlay"]["expected_factor_v2_spec_sha256"],
        "expected_overlay_artifact_sha256": spec["overlay"]["expected_artifact_sha256"],
        "expected_overlay_manifest_file_sha256": spec["overlay"][
            "expected_manifest_file_sha256"
        ],
        "frozen_source_root": spec["frozen_source"]["root"],
        "overlay_manifest_path": spec["overlay"]["manifest_path"],
        "parent_materialization_manifest_path": spec["parent"]["manifest_path"],
        "pycache_prefix": str(pycache_prefix),
        "runtime_dependency_files_physical_sha256": {
            item["relative_path"]: item["sha256"]
            for item in runtime_manifest["files"]
        },
        "runtime_dependency_root": runtime_manifest["site_packages_root"],
        "runtime_required_modules": list(_REQUIRED_RUNTIME_MODULES),
        "schema": CHILD_INPUT_SCHEMA,
        "suspension_metadata_path": spec["suspension"]["metadata_path"],
    }


_MANIFEST_IMPORT_GUARD_SOURCE = r'''
class ManifestImportGuard:
    def __init__(self):
        self.path_finder = importlib.machinery.PathFinder

    def _verified_path(self, candidate, root, bindings, label):
        resolved = Path(candidate).resolve(strict=True)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            return None
        expected = bindings.get(relative)
        if (
            expected is None
            or hashlib.sha256(resolved.read_bytes()).hexdigest() != expected
        ):
            raise ImportError(label)
        return resolved

    def _app_spec(self, fullname):
        relative_base = fullname.replace(".", "/")
        module_path = source_root / f"{relative_base}.py"
        package_path = source_root / relative_base / "__init__.py"
        matches = []
        for candidate, is_package in ((module_path, False), (package_path, True)):
            relative = candidate.relative_to(source_root).as_posix()
            if relative in app_source_sha256:
                matches.append((candidate, is_package))
        if len(matches) != 1:
            raise ImportError("frozen app exact module mapping rejected")
        candidate, is_package = matches[0]
        verified = self._verified_path(
            candidate,
            source_root,
            app_source_sha256,
            "frozen app pre-execution hash rejected",
        )
        locations = [str(verified.parent)] if is_package else None
        return importlib.util.spec_from_file_location(
            fullname,
            verified,
            submodule_search_locations=locations,
        )

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app" or fullname.startswith("app."):
            return self._app_spec(fullname)
        spec = self.path_finder.find_spec(fullname, path, target)
        if spec is None:
            return None
        origin = getattr(spec, "origin", None)
        if origin not in (None, "built-in", "frozen"):
            resolved = Path(origin).resolve(strict=True)
            try:
                resolved.relative_to(source_root)
            except ValueError:
                pass
            else:
                raise ImportError("non-app frozen source import rejected")
            self._verified_path(
                resolved,
                runtime_root,
                runtime_file_sha256,
                "runtime dependency pre-execution hash rejected",
            )
        return spec


def install_manifest_import_guard():
    guard = ManifestImportGuard()
    for index, finder in enumerate(sys.meta_path):
        if finder is importlib.machinery.PathFinder:
            sys.meta_path[index] = guard
            return guard
    raise RuntimeError("standard path finder unavailable")
'''.strip()


_CHILD_BOOTSTRAP = r'''
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

raw_input = sys.stdin.buffer.read()
payload = json.loads(raw_input.decode("utf-8"))
expected_input_fields = {
    "allowed_import_network_event_sequences",
    "attested_app_source_physical_sha256",
    "expected_factor_v2_spec_sha256",
    "expected_overlay_artifact_sha256",
    "expected_overlay_manifest_file_sha256",
    "frozen_source_root",
    "overlay_manifest_path",
    "parent_materialization_manifest_path",
    "pycache_prefix",
    "runtime_dependency_files_physical_sha256",
    "runtime_dependency_root",
    "runtime_required_modules",
    "schema",
    "suspension_metadata_path",
}
if (
    set(payload) != expected_input_fields
    or payload.get("schema") != "factor-v3-parent-source-child-input/v1"
    or canonical(payload) != raw_input
    or sys.flags.isolated != 1
    or sys.flags.dont_write_bytecode != 1
    or sys.flags.no_site != 1
):
    raise ValueError("child input rejected")

network_state = {
    "phase": "dependency_import",
    "events": [],
    "socket_id": None,
}

def deny_network(event, arguments):
    if event == "import" and arguments and arguments[0] == "site":
        raise RuntimeError("site processing denied")
    if event == "open" and arguments and str(arguments[0]).lower().endswith(".pth"):
        raise RuntimeError("pth processing denied")
    if event in {"urllib.Request", "http.client.connect"}:
        raise RuntimeError("network denied")
    if not event.startswith("socket."):
        return
    if network_state["phase"] == "dependency_import" and event == "socket.gethostname":
        if network_state["events"] in ([], ["new", "bind"]):
            network_state["events"].append("gethostname")
            return
    if network_state["phase"] == "dependency_import" and event == "socket.__new__":
        family, socket_type, protocol = map(int, arguments[1:4])
        if (
            network_state["events"] in ([], ["gethostname"])
            and family == 23
            and socket_type == 1
            and protocol == 0
        ):
            network_state["socket_id"] = id(arguments[0])
            network_state["events"].append("new")
            return
    if network_state["phase"] == "dependency_import" and event == "socket.bind":
        address = arguments[1]
        if (
            network_state["events"] in (["new"], ["gethostname", "new"])
            and id(arguments[0]) == network_state["socket_id"]
            and address == ("::1", 0)
        ):
            network_state["events"].append("bind")
            return
    raise RuntimeError("network denied")

sys.addaudithook(deny_network)
source_root = Path(payload["frozen_source_root"]).resolve(strict=True)
runtime_root = Path(payload["runtime_dependency_root"]).resolve(strict=True)
runtime_file_sha256 = payload["runtime_dependency_files_physical_sha256"]
runtime_required_modules = payload["runtime_required_modules"]
allowed_network_sequences = payload["allowed_import_network_event_sequences"]
if (
    type(runtime_file_sha256) is not dict
    or not runtime_file_sha256
    or runtime_required_modules != ["numpy", "pandas", "requests", "xgboost"]
    or allowed_network_sequences
    != [["gethostname", "new", "bind"], ["new", "bind", "gethostname"]]
    or any(
        type(relative) is not str
        or not relative
        or type(digest) is not str
        or len(digest) != 64
        for relative, digest in runtime_file_sha256.items()
    )
):
    raise ValueError("child runtime dependency closure rejected")
app_source_sha256 = payload["attested_app_source_physical_sha256"]
if (
    type(app_source_sha256) is not dict
    or not app_source_sha256
    or any(
        type(relative) is not str
        or not relative.startswith("app/")
        or not relative.endswith(".py")
        or type(digest) is not str
        or len(digest) != 64
        for relative, digest in app_source_sha256.items()
    )
):
    raise ValueError("child app source closure rejected")
__MANIFEST_IMPORT_GUARD_SOURCE__
pycache_root = Path(payload["pycache_prefix"])
pycache_metadata = pycache_root.lstat()
if (
    not pycache_root.is_absolute()
    or pycache_root.is_symlink()
    or getattr(pycache_metadata, "st_file_attributes", 0) & 0x400
    or not pycache_root.is_dir()
    or any(pycache_root.iterdir())
    or sys.pycache_prefix != str(pycache_root)
):
    raise ValueError("child pycache isolation rejected")
try:
    pycache_root.resolve(strict=True).relative_to(source_root)
except ValueError:
    pass
else:
    raise ValueError("child pycache prefix escaped isolation")
sys.path.insert(0, str(runtime_root))
manifest_import_guard = install_manifest_import_guard()
for module_name in runtime_required_modules:
    importlib.import_module(module_name)
for module_name in runtime_required_modules:
    module_path = Path(sys.modules[module_name].__file__).resolve(strict=True)
    try:
        relative_name = module_path.relative_to(runtime_root).as_posix()
    except ValueError as exc:
        raise RuntimeError("required runtime dependency prevalidation rejected") from exc
    if (
        relative_name not in runtime_file_sha256
        or hashlib.sha256(module_path.read_bytes()).hexdigest()
        != runtime_file_sha256[relative_name]
    ):
        raise RuntimeError("required runtime dependency prevalidation rejected")
if network_state["events"] not in allowed_network_sequences:
    raise RuntimeError("dependency import network probe contract rejected")
network_state["phase"] = "strict_deny"
from app.audited_pit_factor_v2_parent import verify_preregistered_development_4_parent
from app.audited_pit_factor_v2_training_overlay import verify_preregistered_development_4_factor_v2_training_overlay

parent = verify_preregistered_development_4_parent(
    payload["parent_materialization_manifest_path"]
)
overlay = verify_preregistered_development_4_factor_v2_training_overlay(
    payload["overlay_manifest_path"],
    parent_materialization_manifest_path=payload["parent_materialization_manifest_path"],
    suspension_metadata_path=payload["suspension_metadata_path"],
    expected_artifact_sha256=payload["expected_overlay_artifact_sha256"],
    expected_manifest_file_sha256=payload["expected_overlay_manifest_file_sha256"],
    expected_factor_v2_spec_sha256=payload["expected_factor_v2_spec_sha256"],
)
origins = []
runtime_origins = []
for module_name, module in sorted(sys.modules.items()):
    if module_name != "app" and not module_name.startswith("app."):
        continue
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        continue
    module_path = Path(module_file).resolve(strict=True)
    try:
        relative = module_path.relative_to(source_root)
    except ValueError as exc:
        raise RuntimeError("loaded app module escaped frozen root") from exc
    if module_path.suffix != ".py" or "__pycache__" in module_path.parts:
        raise RuntimeError("loaded app module is not frozen source")
    if (
        relative.as_posix() not in app_source_sha256
        or hashlib.sha256(module_path.read_bytes()).hexdigest()
        != app_source_sha256[relative.as_posix()]
    ):
        raise RuntimeError("loaded app module escaped attested source closure")
    module_cached = getattr(module, "__cached__", None)
    if module_cached is not None:
        cache_path = Path(module_cached)
        try:
            cache_path.relative_to(pycache_root)
        except ValueError as exc:
            raise RuntimeError("loaded app module cache escaped isolated prefix") from exc
        if cache_path.exists():
            raise RuntimeError("loaded app module consumed bytecode cache")
    origins.append({"module": module_name, "relative_path": relative.as_posix()})
for module_name, module in sorted(sys.modules.items()):
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        continue
    module_path = Path(module_file).resolve(strict=True)
    try:
        relative = module_path.relative_to(runtime_root)
    except ValueError:
        if "site-packages" in {part.lower() for part in module_path.parts}:
            raise RuntimeError("loaded runtime dependency escaped attested root")
        continue
    relative_name = relative.as_posix()
    if (
        relative_name not in runtime_file_sha256
        or hashlib.sha256(module_path.read_bytes()).hexdigest()
        != runtime_file_sha256[relative_name]
    ):
        raise RuntimeError("loaded runtime dependency escaped attested closure")
    runtime_origins.append({"module": module_name, "relative_path": relative_name})
required_modules = {
    "app.audited_pit_factor_v2_parent",
    "app.audited_pit_factor_v2_training_overlay",
}
if not required_modules <= {item["module"] for item in origins}:
    raise RuntimeError("required frozen modules missing")
if not set(runtime_required_modules) <= {item["module"] for item in runtime_origins}:
    raise RuntimeError("required runtime dependencies missing")
if any(pycache_root.iterdir()):
    raise RuntimeError("child pycache isolation mutated")
unsigned = {
    "automatic_trading_eligible": False,
    "development_only": True,
    "embargo_consumed": False,
    "experiment_launch_eligible": False,
    "final_oos_consumed": False,
    "formal_materialization_eligible": False,
    "import_network_event_sequence": network_state["events"],
    "isolated_runtime_verified": True,
    "loaded_app_module_origins_sha256": hashlib.sha256(canonical(origins)).hexdigest(),
    "loaded_runtime_dependency_origins_sha256": hashlib.sha256(canonical(runtime_origins)).hexdigest(),
    "loopback_import_probe_count": 1,
    "model_training_started": False,
    "network_denied": True,
    "oof_scoring_started": False,
    "orders_submitted": False,
    "overlay_public_verification": overlay,
    "parent_public_verification": parent,
    "production_profile_registered": False,
    "production_recommendation_eligible": False,
    "producer_binding_verified": False,
    "public_verifier_replay_performed": True,
    "recommendation_generation_eligible": False,
    "runtime_dependency_authority_verified": False,
    "schema": "factor-v3-parent-source-public-verification/v1",
    "source_authority_complete": False,
}
result = dict(unsigned)
result["result_sha256"] = hashlib.sha256(canonical(unsigned)).hexdigest()
sys.stdout.buffer.write(canonical(result))
'''.replace(
    "__MANIFEST_IMPORT_GUARD_SOURCE__",
    _MANIFEST_IMPORT_GUARD_SOURCE,
).strip()


_RUNTIME_IMPORT_SMOKE_BOOTSTRAP = r'''
import importlib
import json
from pathlib import Path
import sys

raw = sys.stdin.buffer.read()
payload = json.loads(raw.decode("utf-8"))
if (
    set(payload) != {"dependency_roots", "required_modules"}
    or payload["required_modules"] != ["numpy", "pandas", "requests", "xgboost"]
    or sys.flags.isolated != 1
    or sys.flags.no_site != 1
    or sys.flags.dont_write_bytecode != 1
):
    raise ValueError("runtime import smoke input rejected")
state = {"phase": "dependency_import", "events": [], "socket_id": None}

def audit(event, arguments):
    if event == "import" and arguments and arguments[0] == "site":
        raise RuntimeError("site processing denied")
    if event == "open" and arguments and str(arguments[0]).lower().endswith(".pth"):
        raise RuntimeError("pth processing denied")
    if event in {"urllib.Request", "http.client.connect"}:
        raise RuntimeError("network denied")
    if not event.startswith("socket."):
        return
    if state["phase"] == "dependency_import" and event == "socket.gethostname":
        if state["events"] in ([], ["new", "bind"]):
            state["events"].append("gethostname")
            return
    if state["phase"] == "dependency_import" and event == "socket.__new__":
        family, socket_type, protocol = map(int, arguments[1:4])
        if (
            state["events"] in ([], ["gethostname"])
            and family == 23
            and socket_type == 1
            and protocol == 0
        ):
            state["socket_id"] = id(arguments[0])
            state["events"].append("new")
            return
    if state["phase"] == "dependency_import" and event == "socket.bind":
        if (
            state["events"] in (["new"], ["gethostname", "new"])
            and id(arguments[0]) == state["socket_id"]
            and arguments[1] == ("::1", 0)
        ):
            state["events"].append("bind")
            return
    raise RuntimeError("network denied")

sys.addaudithook(audit)
roots = [str(Path(item).resolve(strict=True)) for item in payload["dependency_roots"]]
sys.path[:0] = roots
for name in payload["required_modules"]:
    importlib.import_module(name)
if state["events"] not in (
    ["gethostname", "new", "bind"],
    ["new", "bind", "gethostname"],
):
    raise RuntimeError("dependency import network probe contract rejected")
state["phase"] = "strict_deny"
report = {
    "isolated": True,
    "import_network_event_sequence": state["events"],
    "loopback_import_probe_count": 1,
    "no_site": True,
    "required_modules": payload["required_modules"],
}
sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")))
'''.strip()


def _isolated_runtime_import_smoke_for_test(
    *,
    python_executable: Path,
    dependency_roots: Sequence[Path],
) -> dict[str, Any]:
    roots = [Path(item).resolve(strict=True) for item in dependency_roots]
    with tempfile.TemporaryDirectory(prefix="factor-v3-runtime-smoke-pycache-") as temporary:
        command = [
            str(python_executable),
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={temporary}",
            "-c",
            _RUNTIME_IMPORT_SMOKE_BOOTSTRAP,
        ]
        completed = subprocess.run(
            command,
            cwd=str(roots[0]),
            env=_child_environment(),
            input=_canonical_bytes(
                {
                    "dependency_roots": [str(item) for item in roots],
                    "required_modules": list(_REQUIRED_RUNTIME_MODULES),
                }
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=IMPORT_SMOKE_TIMEOUT_SECONDS,
        )
    if completed.returncode != 0 or completed.stderr:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated runtime dependency import smoke rejected"
        )
    return _strict_json(
        completed.stdout,
        label="runtime dependency import smoke",
        max_bytes=64 * 1024,
    )


def _child_environment() -> dict[str, str]:
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def _open_child_log(root: Path, name: str) -> _OwnedFile:
    _assert_safe_path(root, label="run root", regular=False)
    return _OwnedFile(root / name, label="controlled child log", raw=b"")


def _read_child_log(handle: _OwnedFile, *, max_bytes: int) -> bytes:
    return handle.capture(max_bytes=max_bytes)


def _close_child_log(handle: _OwnedFile | None) -> None:
    if handle is None:
        return
    path = handle.path
    try:
        handle.verify()
    finally:
        try:
            handle.close()
        finally:
            path.unlink(missing_ok=True)


def _create_isolated_pycache_prefix(root: Path) -> Path:
    _assert_safe_path(root, label="run root", regular=False)
    for _attempt in range(16):
        path = root / f".pycache-prefix-{secrets.token_hex(16)}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "isolated pycache prefix creation rejected"
            ) from exc
        _assert_safe_path(path, label="isolated pycache prefix", regular=False)
        if any(path.iterdir()):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "isolated pycache prefix not empty"
            )
        return path
    raise FactorV3ParentSourceAuthorityRunnerError(
        "isolated pycache prefix collision rejected"
    )


def _create_isolated_child_cwd(root: Path) -> Path:
    _assert_safe_path(root, label="run root", regular=False)
    for _attempt in range(16):
        path = root / f".child-cwd-{secrets.token_hex(16)}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "isolated child working directory creation rejected"
            ) from exc
        _assert_safe_path(path, label="isolated child working directory", regular=False)
        if any(path.iterdir()):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "isolated child working directory not empty"
            )
        return path
    raise FactorV3ParentSourceAuthorityRunnerError(
        "isolated child working directory collision rejected"
    )


def _remove_isolated_child_cwd(path: Path) -> None:
    _assert_safe_path(path, label="isolated child working directory", regular=False)
    if any(path.iterdir()):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated child working directory mutated"
        )
    try:
        path.rmdir()
    except OSError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated child working directory cleanup rejected"
        ) from exc
    if os.path.lexists(path):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated child working directory cleanup incomplete"
        )


def _remove_isolated_pycache_prefix(path: Path) -> None:
    _assert_safe_path(path, label="isolated pycache prefix", regular=False)
    if any(path.iterdir()):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated pycache prefix mutated"
        )
    try:
        path.rmdir()
    except OSError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated pycache prefix cleanup rejected"
        ) from exc
    if os.path.lexists(path):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "isolated pycache prefix cleanup incomplete"
        )


def _assign_windows_job(process: Any) -> Any:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BASIC_LIMIT(ctypes.Structure):
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

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise FactorV3ParentSourceAuthorityRunnerError("Windows Job Object creation rejected")
    limits = EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = 0x00002000 | 0x00000008
    limits.BasicLimitInformation.ActiveProcessLimit = 64
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        raise FactorV3ParentSourceAuthorityRunnerError("Windows Job Object policy rejected")
    if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        kernel.CloseHandle(job)
        raise FactorV3ParentSourceAuthorityRunnerError("Windows Job Object assignment rejected")
    return job


def _resume_suspended_process(process: Any) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    function = ctypes.windll.ntdll.NtResumeProcess
    function.argtypes = (wintypes.HANDLE,)
    function.restype = ctypes.c_long
    if function(wintypes.HANDLE(process._handle)) != 0:
        try:
            process.kill()
        except OSError:
            pass
        raise FactorV3ParentSourceAuthorityRunnerError(
            "suspended child process resume rejected"
        )


def _terminate_windows_job(job: Any) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    function = ctypes.windll.kernel32.TerminateJobObject
    function.argtypes = (wintypes.HANDLE, wintypes.UINT)
    function.restype = wintypes.BOOL
    if not function(job, 1):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "Windows Job Object termination rejected"
        )


def _kill_and_wait_process(process: Any, *, timeout_seconds: int) -> int:
    try:
        process.kill()
    except OSError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "uncontained child process termination rejected"
        ) from exc
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "uncontained child process wait rejected"
        ) from exc
    if type(exit_code) is not int or process.returncode != exit_code:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "uncontained child process exit rejected"
        )
    return exit_code


def _wait_for_windows_job_tree(job: Any, *, timeout_seconds: int) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class ACCOUNTING(ctypes.Structure):
        _fields_ = (
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        )

    query = ctypes.windll.kernel32.QueryInformationJobObject
    query.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    query.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout_seconds
    while True:
        accounting = ACCOUNTING()
        if not query(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "Windows Job Object accounting rejected"
            )
        if accounting.ActiveProcesses == 0:
            return
        if time.monotonic() >= deadline:
            _terminate_windows_job(job)
            raise FactorV3ParentSourceAuthorityRunnerError(
                "Windows Job Object process tree drain timeout rejected"
            )
        time.sleep(0.01)


def _close_windows_job(job: Any) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes

    ctypes.windll.kernel32.CloseHandle(job)


class _BoundedPipeCapture:
    def __init__(
        self,
        stream: Any,
        *,
        max_bytes: int,
        terminate: Any,
        overflow_event: threading.Event,
    ) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        self.terminate = terminate
        self.overflow_event = overflow_event
        self.digest = hashlib.sha256()
        self.size_bytes = 0
        self.chunks: list[bytes] = []
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self) -> None:
        try:
            while True:
                chunk = self.stream.read(64 * 1024)
                if not chunk:
                    break
                self.digest.update(chunk)
                self.size_bytes += len(chunk)
                if self.size_bytes <= self.max_bytes:
                    self.chunks.append(chunk)
                elif not self.overflow_event.is_set():
                    self.overflow_event.set()
                    self.terminate()
        except BaseException as exc:
            self.error = exc
            if not self.overflow_event.is_set():
                self.overflow_event.set()
                self.terminate()
        finally:
            try:
                self.stream.close()
            except OSError:
                pass

    def start(self) -> None:
        self.thread.start()

    def finish(self, *, timeout_seconds: int) -> tuple[bytes | None, dict[str, Any]]:
        self.thread.join(timeout_seconds)
        if self.thread.is_alive():
            self.terminate()
            raise FactorV3ParentSourceAuthorityRunnerError(
                "child output pipe drain timeout rejected"
            )
        if self.error is not None:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "child output pipe read rejected"
            ) from self.error
        descriptor = {
            "sha256": self.digest.hexdigest(),
            "size_bytes": self.size_bytes,
        }
        if self.size_bytes > self.max_bytes:
            return None, descriptor
        return b"".join(self.chunks), descriptor


def _open_child_pipe_pair() -> tuple[Any, Any]:
    read_fd: int | None = None
    write_fd: int | None = None
    read_handle: Any = None
    try:
        read_fd, write_fd = os.pipe()
        read_handle = os.fdopen(read_fd, "rb", buffering=0)
        read_fd = None
        write_handle = os.fdopen(write_fd, "wb", buffering=0)
        write_fd = None
        return read_handle, write_handle
    except BaseException:
        if read_handle is not None:
            try:
                read_handle.close()
            except OSError:
                pass
        if read_fd is not None:
            try:
                os.close(read_fd)
            except OSError:
                pass
        if write_fd is not None:
            try:
                os.close(write_fd)
            except OSError:
                pass
        raise


def _spawn_child(
    spec: Mapping[str, Any],
    *,
    app_sources: Mapping[str, Mapping[str, str]],
    runtime_manifest: Mapping[str, Any],
    run_root: Path,
    stdout_handle: Any,
    stderr_handle: Any,
) -> int:
    child = spec["subprocess"]
    pycache_prefix = _create_isolated_pycache_prefix(run_root)
    try:
        child_cwd = _create_isolated_child_cwd(run_root)
    except BaseException:
        _remove_isolated_pycache_prefix(pycache_prefix)
        raise
    python_flags = [
        flag.replace(_PYCACHE_PREFIX_TOKEN, str(pycache_prefix))
        for flag in child["python_flags"]
    ]
    command = [child["python_executable"], *python_flags, "-c", _CHILD_BOOTSTRAP]
    job = None
    process: Any = None
    exit_code: int | None = None
    error: BaseException | None = None
    stdout_descriptor: dict[str, Any] | None = None
    stderr_descriptor: dict[str, Any] | None = None
    pipe_handles: list[Any] = []
    unowned_read_handles: list[Any] = []
    captures: list[_BoundedPipeCapture] = []
    overflow_event = threading.Event()
    try:
        stdout_read, stdout_write = _open_child_pipe_pair()
        pipe_handles.append(stdout_write)
        unowned_read_handles.append(stdout_read)
        stderr_read, stderr_write = _open_child_pipe_pair()
        pipe_handles.append(stderr_write)
        unowned_read_handles.append(stderr_read)
        kwargs: dict[str, Any] = {
            "close_fds": True,
            "cwd": str(child_cwd),
            "env": _child_environment(),
            "shell": False,
            "stdin": subprocess.PIPE,
            "stdout": stdout_write,
            "stderr": stderr_write,
        }
        if os.name == "nt":
            kwargs["creationflags"] = _CREATE_SUSPENDED
        process = subprocess.Popen(command, **kwargs)
        job = _assign_windows_job(process)

        def terminate_for_output_boundary() -> None:
            if job is not None:
                _terminate_windows_job(job)
            elif process is not None:
                try:
                    process.kill()
                except OSError:
                    pass

        captures = [
            _BoundedPipeCapture(
                stdout_read,
                max_bytes=child["max_stdout_bytes"],
                terminate=terminate_for_output_boundary,
                overflow_event=overflow_event,
            ),
            _BoundedPipeCapture(
                stderr_read,
                max_bytes=child["max_stderr_bytes"],
                terminate=terminate_for_output_boundary,
                overflow_event=overflow_event,
            ),
        ]
        unowned_read_handles.clear()
        for capture in captures:
            capture.start()
        _resume_suspended_process(process)
        try:
            process.communicate(
                input=_canonical_bytes(
                    _child_input(
                        spec,
                        app_sources=app_sources,
                        pycache_prefix=pycache_prefix,
                        runtime_manifest=runtime_manifest,
                    )
                ),
                timeout=child["timeout_seconds"],
            )
        except subprocess.TimeoutExpired as exc:
            if job is not None:
                _terminate_windows_job(job)
            else:
                process.kill()
            process.communicate()
            error = FactorV3ParentSourceAuthorityRunnerError(
                "child verifier timeout rejected"
            )
            error.__cause__ = exc
        exit_code = process.returncode
        while pipe_handles:
            pipe_handles.pop().close()
        stdout_raw, stdout_descriptor = captures[0].finish(
            timeout_seconds=min(child["timeout_seconds"], 60)
        )
        stderr_raw, stderr_descriptor = captures[1].finish(
            timeout_seconds=min(child["timeout_seconds"], 60)
        )
        stdout_handle.rewrite_terminal(b"" if stdout_raw is None else stdout_raw)
        stderr_handle.rewrite_terminal(b"" if stderr_raw is None else stderr_raw)
        if stdout_raw is None or stderr_raw is None:
            error = FactorV3ParentSourceAuthorityRunnerError(
                "child output size rejected during execution"
            )
        if exit_code is None and error is None:
            error = FactorV3ParentSourceAuthorityRunnerError(
                "child process exit unavailable"
            )
    except OSError as exc:
        error = FactorV3ParentSourceAuthorityRunnerError(
            "child process launch rejected"
        )
        error.__cause__ = exc
    except BaseException as exc:
        error = exc
    finally:
        while pipe_handles:
            try:
                pipe_handles.pop().close()
            except OSError:
                pass
        if process is not None and error is not None and process.returncode is None:
            try:
                if job is not None:
                    _terminate_windows_job(job)
                else:
                    exit_code = _kill_and_wait_process(
                        process,
                        timeout_seconds=min(child["timeout_seconds"], 60),
                    )
            except BaseException as exc:
                error = exc
        if job is not None:
            try:
                _wait_for_windows_job_tree(
                    job,
                    timeout_seconds=min(child["timeout_seconds"], 60),
                )
                if process is not None and process.returncode is None:
                    exit_code = process.wait(
                        timeout=min(child["timeout_seconds"], 60)
                    )
            except BaseException as exc:
                error = exc
            finally:
                _close_windows_job(job)
            if process is not None and process.returncode is None:
                try:
                    exit_code = process.wait(
                        timeout=min(child["timeout_seconds"], 60)
                    )
                except BaseException as exc:
                    error = exc
        if process is not None and exit_code is None:
            exit_code = process.returncode
        while unowned_read_handles:
            try:
                unowned_read_handles.pop().close()
            except OSError:
                pass
        if captures and (stdout_descriptor is None or stderr_descriptor is None):
            try:
                stdout_raw, stdout_descriptor = captures[0].finish(
                    timeout_seconds=min(child["timeout_seconds"], 60)
                )
                stderr_raw, stderr_descriptor = captures[1].finish(
                    timeout_seconds=min(child["timeout_seconds"], 60)
                )
                stdout_handle.rewrite_terminal(
                    b"" if stdout_raw is None else stdout_raw
                )
                stderr_handle.rewrite_terminal(
                    b"" if stderr_raw is None else stderr_raw
                )
            except BaseException as exc:
                error = exc
        try:
            _remove_isolated_pycache_prefix(pycache_prefix)
        except BaseException as exc:
            if error is None:
                error = exc
        try:
            _remove_isolated_child_cwd(child_cwd)
        except BaseException as exc:
            if error is None:
                error = exc
    if error is not None:
        raise _ChildBoundaryError(
            str(error),
            child_exit_code=exit_code,
            child_stdout=stdout_descriptor,
            child_stderr=stderr_descriptor,
        ) from error
    if exit_code is None:
        raise _ChildBoundaryError(
            "child process exit unavailable",
            child_exit_code=None,
        )
    return exit_code


def _validate_child_result(raw: bytes, spec: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = _strict_json(
            raw,
            label="child result",
            max_bytes=spec["subprocess"]["max_stdout_bytes"],
        )
    except FactorV3ParentSourceAuthorityRunnerError as exc:
        raise FactorV3ParentSourceAuthorityRunnerError("child result rejected") from exc
    expected_fields = {
        "development_only",
        "formal_materialization_eligible",
        "import_network_event_sequence",
        "isolated_runtime_verified",
        "loaded_app_module_origins_sha256",
        "loaded_runtime_dependency_origins_sha256",
        "loopback_import_probe_count",
        "network_denied",
        "overlay_public_verification",
        "parent_public_verification",
        "public_verifier_replay_performed",
        "result_sha256",
        "schema",
        *SAFETY_FALSE_FIELDS,
    }
    if set(result) != expected_fields or result.get("schema") != CHILD_RESULT_SCHEMA:
        raise FactorV3ParentSourceAuthorityRunnerError("child result fields rejected")
    _scope_false(result, label="child result")
    if result.get("public_verifier_replay_performed") is not True:
        raise FactorV3ParentSourceAuthorityRunnerError("child result verifier replay rejected")
    if (
        result.get("isolated_runtime_verified") is not True
        or result.get("network_denied") is not True
        or result.get("import_network_event_sequence")
        not in spec["runtime_dependencies"]["allowed_import_network_event_sequences"]
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("child result isolation rejected")
    _strict_sha256(
        result.get("loaded_app_module_origins_sha256"),
        label="child loaded module origins",
    )
    _strict_sha256(
        result.get("loaded_runtime_dependency_origins_sha256"),
        label="child loaded runtime dependency origins",
    )
    if (
        type(result.get("loopback_import_probe_count")) is not int
        or result["loopback_import_probe_count"] != 1
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "child loopback import probe rejected"
        )
    unsigned = dict(result)
    result_sha = _strict_sha256(unsigned.pop("result_sha256"), label="child result")
    if result_sha != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceAuthorityRunnerError("child result self hash rejected")
    parent = result["parent_public_verification"]
    parent_fields = {
        "artifact_sha256",
        "feature_row_count",
        "fold_count",
        "folds_sha256",
        "manifest_file_sha256",
        "outcome_row_count",
        "stage_bar_row_count",
        "verified",
    }
    overlay = result["overlay_public_verification"]
    overlay_fields = {
        "artifact_sha256",
        "eligible_candidate_count",
        "excluded_candidate_count",
        "feature_row_count",
        "formal_preregistration_verified",
        "manifest_file_sha256",
        "overlay_file_sha256",
        "parent_artifact_sha256",
        "verified",
    }
    if type(parent) is not dict or set(parent) != parent_fields:
        raise FactorV3ParentSourceAuthorityRunnerError("child parent result rejected")
    if type(overlay) is not dict or set(overlay) != overlay_fields:
        raise FactorV3ParentSourceAuthorityRunnerError("child overlay result rejected")
    for field in ("feature_row_count", "fold_count", "outcome_row_count", "stage_bar_row_count"):
        if type(parent.get(field)) is not int or parent[field] < 0:
            raise FactorV3ParentSourceAuthorityRunnerError("child parent count rejected")
    for field in ("eligible_candidate_count", "excluded_candidate_count", "feature_row_count"):
        if type(overlay.get(field)) is not int or overlay[field] < 0:
            raise FactorV3ParentSourceAuthorityRunnerError("child overlay count rejected")
    if (
        parent.get("verified") is not True
        or parent.get("artifact_sha256") != spec["parent"]["expected_artifact_sha256"]
        or parent.get("manifest_file_sha256")
        != spec["parent"]["expected_manifest_file_sha256"]
        or overlay.get("verified") is not True
        or overlay.get("formal_preregistration_verified") is not True
        or overlay.get("artifact_sha256") != spec["overlay"]["expected_artifact_sha256"]
        or overlay.get("manifest_file_sha256")
        != spec["overlay"]["expected_manifest_file_sha256"]
        or overlay.get("parent_artifact_sha256") != spec["parent"]["expected_artifact_sha256"]
        or parent.get("feature_row_count") != PARENT_FEATURE_ROW_COUNT
        or parent.get("outcome_row_count") != PARENT_OUTCOME_ROW_COUNT
        or parent.get("fold_count") != PARENT_FOLD_COUNT
        or parent.get("folds_sha256") != PARENT_FOLDS_SHA256
        or overlay.get("eligible_candidate_count") != OVERLAY_ELIGIBLE_CANDIDATE_COUNT
        or overlay.get("excluded_candidate_count") != OVERLAY_EXCLUDED_CANDIDATE_COUNT
        or overlay.get("feature_row_count") != OVERLAY_ELIGIBLE_CANDIDATE_COUNT
        or overlay.get("eligible_candidate_count") + overlay.get("excluded_candidate_count")
        != parent.get("feature_row_count")
    ):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "child parent or overlay public verifier result rejected"
        )
    _strict_sha256(parent.get("folds_sha256"), label="child parent folds")
    _strict_sha256(overlay.get("overlay_file_sha256"), label="child overlay file")
    return result


def _receipt(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    child_result: Mapping[str, Any],
    child_stdout: Mapping[str, Any],
    child_stderr: Mapping[str, Any],
    preflight: Mapping[str, Any],
    postflight: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "authority_status": "CANDIDATE_PARENT_REPLAY_ONLY",
        "candidate_verified": True,
        "child_result_sha256": child_result["result_sha256"],
        "child_stderr": dict(child_stderr),
        "child_stdout": dict(child_stdout),
        "claim_sha256": claim_sha256,
        "development_only": True,
        "formal_materialization_eligible": False,
        "overlay_artifact_sha256": spec["overlay"]["expected_artifact_sha256"],
        "overlay_manifest_file_sha256": spec["overlay"]["expected_manifest_file_sha256"],
        "parent_artifact_sha256": spec["parent"]["expected_artifact_sha256"],
        "parent_manifest_file_sha256": spec["parent"]["expected_manifest_file_sha256"],
        "postflight_attestation": dict(postflight),
        "postflight_attestation_sha256": postflight["attestation_sha256"],
        "preflight_attestation": dict(preflight),
        "preflight_attestation_sha256": preflight["attestation_sha256"],
        "public_verifier_replay_performed": True,
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": RECEIPT_SCHEMA,
        "source_authority_verified": False,
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "candidate_root_sha256": _canonical_sha256(unsigned)}


def _failure(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    stage: str,
    error: BaseException,
    child_exit_code: int | None,
    child_stdout: Mapping[str, Any] | None,
    child_stderr: Mapping[str, Any] | None,
    preflight: Mapping[str, Any] | None,
    postflight: Mapping[str, Any] | None,
) -> dict[str, Any]:
    unsigned = {
        "child_exit_code": child_exit_code,
        "child_stderr": None if child_stderr is None else dict(child_stderr),
        "child_stdout": None if child_stdout is None else dict(child_stdout),
        "claim_sha256": claim_sha256,
        "development_only": True,
        "error_type": type(error).__name__,
        "formal_materialization_eligible": False,
        "postflight_attestation_sha256": (
            None if postflight is None else postflight["attestation_sha256"]
        ),
        "preflight_attestation_sha256": (
            None if preflight is None else preflight["attestation_sha256"]
        ),
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": FAILURE_SCHEMA,
        "source_authority_verified": False,
        "stage": stage,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "failure_root_sha256": _canonical_sha256(unsigned)}


def _descriptor(relative: str, file_sha256: str, root_sha256: str) -> dict[str, Any]:
    return {
        "file_sha256": file_sha256,
        "relative_path": relative,
        "root_sha256": root_sha256,
    }


def _result(spec: Mapping[str, Any], receipt_descriptor: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authority_status": "CANDIDATE_PARENT_REPLAY_ONLY",
        "candidate_root_sha256": receipt_descriptor["root_sha256"],
        "candidate_verified": True,
        "development_only": True,
        "formal_materialization_eligible": False,
        "receipt_relative_path": receipt_descriptor["relative_path"],
        "receipt_sha256": receipt_descriptor["file_sha256"],
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": RESULT_SCHEMA,
        "source_authority_verified": False,
        "status": "completed",
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }


def _persist_failure(
    *,
    root: Path,
    spec: Mapping[str, Any],
    claim_sha256: str,
    stage: str,
    error: BaseException,
    child_exit_code: int | None,
    child_stdout: Mapping[str, Any] | None,
    child_stderr: Mapping[str, Any] | None,
    preflight: Mapping[str, Any] | None,
    postflight: Mapping[str, Any] | None,
    ownership: _RunOwnership,
) -> None:
    evidence = _failure(
        spec=spec,
        claim_sha256=claim_sha256,
        stage=stage,
        error=error,
        child_exit_code=child_exit_code,
        child_stdout=child_stdout,
        child_stderr=child_stderr,
        preflight=preflight,
        postflight=postflight,
    )
    relative, file_sha, failure_handle = _create_owned_cas(root, "failures", evidence)
    ownership.add(failure_handle)
    descriptor = _descriptor(relative, file_sha, evidence["failure_root_sha256"])
    status = _status_payload(
        spec=spec,
        claim_sha256=claim_sha256,
        status="failed",
        source_authority_verified=False,
        failure=descriptor,
        child_exit_code=child_exit_code,
        child_stdout=child_stdout,
        child_stderr=child_stderr,
    )
    if ownership.status is None:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "terminal status reservation unavailable"
        )
    ownership.status.rewrite_terminal(_canonical_bytes(status))
    ownership.verify()
    _assert_terminal_output_tree(
        root,
        descriptor,
        category="failures",
        expected_top_files={"claim.json", "run-spec.json", "status.json"},
    )


def run_factor_v3_parent_source_candidate(
    *,
    run_spec_path: str | Path,
    run_root: str | Path,
    expected_run_spec_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Claim once, invoke both frozen public verifiers, then attest and seal."""

    spec = load_factor_v3_parent_source_candidate_run_spec(
        Path(run_spec_path),
        expected_file_sha256=expected_run_spec_file_sha256,
    )
    requested_root = Path(run_root).resolve(strict=False)
    if requested_root != Path(spec["run_root"]):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "canonical run root binding rejected"
        )
    root = _run_root(requested_root, create=True)
    _assert_disjoint(root, spec)
    claim_sha256, ownership = _initialize_run(root, spec)
    stage = "preflight"
    preflight: dict[str, Any] | None = None
    postflight: dict[str, Any] | None = None
    child_exit_code: int | None = None
    child_stdout: dict[str, Any] | None = None
    child_stderr: dict[str, Any] | None = None
    closure: _InputClosure | None = None
    stdout_handle: Any = None
    stderr_handle: Any = None
    try:
        closure = _InputClosure(spec)
        preflight = closure.preflight()
        stage = "child"
        stdout_handle = _open_child_log(root, ".child-stdout.partial")
        stderr_handle = _open_child_log(root, ".child-stderr.partial")
        child_exit_code = _spawn_child(
            spec,
            app_sources=closure.app_sources,
            runtime_manifest=closure.runtime_manifest,
            run_root=root,
            stdout_handle=stdout_handle,
            stderr_handle=stderr_handle,
        )
        stdout = _read_child_log(
            stdout_handle,
            max_bytes=spec["subprocess"]["max_stdout_bytes"],
        )
        stderr = _read_child_log(
            stderr_handle,
            max_bytes=spec["subprocess"]["max_stderr_bytes"],
        )
        child_stdout = _io_descriptor(stdout)
        child_stderr = _io_descriptor(stderr)
        if child_exit_code != 0:
            raise FactorV3ParentSourceAuthorityRunnerError("child verifier exit rejected")
        if stderr:
            raise FactorV3ParentSourceAuthorityRunnerError("child verifier stderr rejected")
        child_result = _validate_child_result(stdout, spec)
        stage = "postflight"
        postflight = closure.postflight()
        if postflight != preflight:
            raise FactorV3ParentSourceAuthorityRunnerError("postflight input changed")
        _close_child_log(stdout_handle)
        stdout_handle = None
        _close_child_log(stderr_handle)
        stderr_handle = None
        receipt = _receipt(
            spec=spec,
            claim_sha256=claim_sha256,
            child_result=child_result,
            child_stdout=child_stdout,
            child_stderr=child_stderr,
            preflight=preflight,
            postflight=postflight,
        )
        relative, file_sha, receipt_handle = _create_owned_cas(
            root,
            "receipts",
            receipt,
        )
        ownership.add(receipt_handle)
        descriptor = _descriptor(relative, file_sha, receipt["candidate_root_sha256"])
        terminal = _status_payload(
                spec=spec,
                claim_sha256=claim_sha256,
                status="completed",
                source_authority_verified=False,
                receipt=descriptor,
                child_exit_code=child_exit_code,
                child_stdout=child_stdout,
                child_stderr=child_stderr,
        )
        if ownership.status is None:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "terminal status reservation unavailable"
            )
        ownership.status.rewrite_terminal(_canonical_bytes(terminal))
        claim_value = _strict_json(
            next(
                handle.expected_raw
                for handle in ownership.handles
                if handle.label == "single attempt claim"
            ),
            label="single attempt claim",
            max_bytes=_MAX_STATE_BYTES,
        )
        terminal_value = _validated_status(terminal, spec)
        _validated_receipt(
            receipt,
            spec=spec,
            status=terminal_value,
            claim=claim_value,
        )
        ownership.verify()
        _assert_terminal_output_tree(
            root,
            descriptor,
            category="receipts",
            expected_top_files={"claim.json", "run-spec.json", "status.json"},
        )
        return _result(spec, descriptor)
    except BaseException as exc:
        if isinstance(exc, _ChildBoundaryError):
            if child_exit_code is None:
                child_exit_code = exc.child_exit_code
            if child_stdout is None:
                child_stdout = exc.child_stdout
            if child_stderr is None:
                child_stderr = exc.child_stderr
        if stdout_handle is not None and child_stdout is None:
            try:
                child_stdout = _io_descriptor(
                    _read_child_log(
                        stdout_handle,
                        max_bytes=spec["subprocess"]["max_stdout_bytes"],
                    )
                )
            except BaseException:
                child_stdout = None
        if stderr_handle is not None and child_stderr is None:
            try:
                child_stderr = _io_descriptor(
                    _read_child_log(
                        stderr_handle,
                        max_bytes=spec["subprocess"]["max_stderr_bytes"],
                    )
                )
            except BaseException:
                child_stderr = None
        _close_child_log(stdout_handle)
        stdout_handle = None
        _close_child_log(stderr_handle)
        stderr_handle = None
        _persist_failure(
            root=root,
            spec=spec,
            claim_sha256=claim_sha256,
            stage=stage,
            error=exc,
            child_exit_code=child_exit_code,
            child_stdout=child_stdout,
            child_stderr=child_stderr,
            preflight=preflight,
            postflight=postflight,
            ownership=ownership,
        )
        if isinstance(exc, FactorV3ParentSourceAuthorityRunnerError):
            raise
        raise FactorV3ParentSourceAuthorityRunnerError("parent source run failed") from exc
    finally:
        if closure is not None:
            closure.close()
        for handle in (stdout_handle, stderr_handle):
            _close_child_log(handle)
        ownership.close()


def _validated_status(value: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    status = _assert_fields(value, _STATUS_FIELDS, label="run status")
    if status.get("schema") != STATUS_SCHEMA or status.get("run_spec_sha256") != spec["run_spec_sha256"]:
        raise FactorV3ParentSourceAuthorityRunnerError("run status binding rejected")
    _scope_false(status, label="run status")
    unsigned = dict(status)
    observed = _strict_sha256(unsigned.pop("status_sha256"), label="run status")
    if observed != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceAuthorityRunnerError("run status self hash rejected")
    if status["status"] == "completed":
        if (
            status["authority_status"] != "CANDIDATE_PARENT_REPLAY_ONLY"
            or status["candidate_verified"] is not True
            or status["verified"] is not False
            or status["source_authority_verified"] is not False
            or status["child_exit_code"] != 0
            or status["receipt"] is None
            or status["failure"] is not None
        ):
            raise FactorV3ParentSourceAuthorityRunnerError("completed status rejected")
        _validated_io_descriptor(status["child_stdout"], label="status child stdout")
        stderr = _validated_io_descriptor(status["child_stderr"], label="status child stderr")
        if stderr != _io_descriptor(b""):
            raise FactorV3ParentSourceAuthorityRunnerError("completed status stderr rejected")
    elif status["status"] == "failed":
        if (
            status["authority_status"] != "CANDIDATE_REPLAY_FAILED"
            or status["candidate_verified"] is not False
            or status["verified"] is not False
            or status["source_authority_verified"] is not False
            or status["failure"] is None
        ):
            raise FactorV3ParentSourceAuthorityRunnerError("failed status rejected")
    else:
        raise FactorV3ParentSourceAuthorityRunnerError("terminal status rejected")
    return status


def _validated_io_descriptor(value: Any, *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"sha256", "size_bytes"}:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} descriptor rejected")
    _strict_sha256(value["sha256"], label=label)
    if type(value["size_bytes"]) is not int or value["size_bytes"] < 0:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{label} size rejected")
    return dict(value)


def _validated_claim_value(value: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "claim_sha256",
        "development_only",
        "external_claim_path_sha256",
        "formal_materialization_eligible",
        "run_root_sha256",
        "run_spec_sha256",
        "schema",
        "single_attempt",
        *SAFETY_FALSE_FIELDS,
    }
    if set(value) != expected_fields or value.get("schema") != CLAIM_SCHEMA:
        raise FactorV3ParentSourceAuthorityRunnerError("single attempt claim fields rejected")
    _scope_false(value, label="single attempt claim")
    if (
        value.get("single_attempt") is not True
        or value.get("run_spec_sha256") != spec["run_spec_sha256"]
        or value.get("run_root_sha256")
        != hashlib.sha256(spec["run_root"].encode("utf-8")).hexdigest()
        or value.get("external_claim_path_sha256")
        != hashlib.sha256(spec["external_run_claim_path"].encode("utf-8")).hexdigest()
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("single attempt claim binding rejected")
    unsigned = dict(value)
    claim_sha = _strict_sha256(unsigned.pop("claim_sha256"), label="single attempt claim")
    if claim_sha != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceAuthorityRunnerError("single attempt claim self hash rejected")
    return value


def _validated_claim(root: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    value, _raw = _read_json(
        root / "claim.json",
        label="single attempt claim",
        max_bytes=_MAX_STATE_BYTES,
    )
    return _validated_claim_value(value, spec)


def _load_cas(root: Path, descriptor: Mapping[str, Any], *, category: str) -> dict[str, Any]:
    if type(descriptor) is not dict or set(descriptor) != {
        "file_sha256",
        "relative_path",
        "root_sha256",
    }:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{category} descriptor rejected")
    file_sha = _strict_sha256(descriptor["file_sha256"], label=category)
    expected_relative = f"{category}/sha256/{file_sha[:2]}/{file_sha}.json"
    if descriptor["relative_path"] != expected_relative:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{category} CAS path rejected")
    path = root / Path(*expected_relative.split("/"))
    value, raw = _read_json(path, label=category, max_bytes=_MAX_STATE_BYTES)
    if hashlib.sha256(raw).hexdigest() != file_sha:
        raise FactorV3ParentSourceAuthorityRunnerError(f"{category} CAS hash rejected")
    return value


def _assert_terminal_output_tree(
    root: Path,
    descriptor: Mapping[str, Any],
    *,
    category: str,
    expected_top_files: set[str],
) -> None:
    expected_relative = Path(*descriptor["relative_path"].split("/"))
    if expected_relative.parts[0] != category:
        raise FactorV3ParentSourceAuthorityRunnerError("terminal CAS category rejected")
    expected_files = {Path(name) for name in expected_top_files} | {expected_relative}
    expected_directories = {
        Path(*expected_relative.parts[:index])
        for index in range(1, len(expected_relative.parts))
    }
    observed_files: set[Path] = set()
    observed_directories: set[Path] = set()
    for member in root.rglob("*"):
        if _is_reparse_point(member):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "terminal output tree reparse rejected"
            )
        relative = member.relative_to(root)
        if member.is_file():
            observed_files.add(relative)
        elif member.is_dir():
            observed_directories.add(relative)
        else:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "terminal output tree member rejected"
            )
    if observed_files != expected_files or observed_directories != expected_directories:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "terminal output tree members rejected"
        )


def _assert_terminal_run_tree(root: Path, descriptor: Mapping[str, Any], *, category: str) -> None:
    _assert_terminal_output_tree(
        root,
        descriptor,
        category=category,
        expected_top_files={"claim.json", "run-spec.json", "status.json"},
    )


def _validated_receipt(
    value: Any,
    *,
    spec: Mapping[str, Any],
    status: Mapping[str, Any],
    claim: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        "candidate_root_sha256",
        "authority_status",
        "candidate_verified",
        "child_result_sha256",
        "child_stderr",
        "child_stdout",
        "claim_sha256",
        "development_only",
        "formal_materialization_eligible",
        "overlay_artifact_sha256",
        "overlay_manifest_file_sha256",
        "parent_artifact_sha256",
        "parent_manifest_file_sha256",
        "postflight_attestation",
        "postflight_attestation_sha256",
        "preflight_attestation",
        "preflight_attestation_sha256",
        "public_verifier_replay_performed",
        "run_spec_sha256",
        "schema",
        "source_authority_verified",
        "verified",
        *SAFETY_FALSE_FIELDS,
    }
    if type(value) is not dict or set(value) != fields or value.get("schema") != RECEIPT_SCHEMA:
        raise FactorV3ParentSourceAuthorityRunnerError("receipt fields rejected")
    _scope_false(value, label="receipt")
    stdout = _validated_io_descriptor(value["child_stdout"], label="receipt child stdout")
    stderr = _validated_io_descriptor(value["child_stderr"], label="receipt child stderr")
    if (
        value.get("authority_status") != "CANDIDATE_PARENT_REPLAY_ONLY"
        or value.get("candidate_verified") is not True
        or value.get("verified") is not False
        or value.get("source_authority_verified") is not False
        or value.get("public_verifier_replay_performed") is not True
        or value.get("run_spec_sha256") != spec["run_spec_sha256"]
        or value.get("claim_sha256") != claim["claim_sha256"]
        or value.get("parent_artifact_sha256") != PARENT_ARTIFACT_SHA256
        or value.get("parent_manifest_file_sha256") != PARENT_MANIFEST_FILE_SHA256
        or value.get("overlay_artifact_sha256") != OVERLAY_ARTIFACT_SHA256
        or value.get("overlay_manifest_file_sha256") != OVERLAY_MANIFEST_FILE_SHA256
        or value.get("preflight_attestation_sha256")
        != value.get("postflight_attestation_sha256")
        or value.get("preflight_attestation") != value.get("postflight_attestation")
        or type(value.get("preflight_attestation")) is not dict
        or value["preflight_attestation"].get("attestation_sha256")
        != value.get("preflight_attestation_sha256")
        or type(value.get("postflight_attestation")) is not dict
        or value["postflight_attestation"].get("attestation_sha256")
        != value.get("postflight_attestation_sha256")
        or stderr != _io_descriptor(b"")
        or stdout != status["child_stdout"]
        or stderr != status["child_stderr"]
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("receipt authority binding rejected")
    _strict_sha256(value["child_result_sha256"], label="receipt child result")
    _strict_sha256(value["preflight_attestation_sha256"], label="receipt preflight")
    unsigned = dict(value)
    candidate_root = _strict_sha256(
        unsigned.pop("candidate_root_sha256"),
        label="receipt candidate",
    )
    if (
        candidate_root != _canonical_sha256(unsigned)
        or candidate_root != status["receipt"]["root_sha256"]
    ):
        raise FactorV3ParentSourceAuthorityRunnerError("receipt authority root rejected")
    return value


class _HeldRunEvidenceFile:
    def __init__(self, path: Path, *, label: str) -> None:
        self.path = path
        self.label = label
        self.descriptor = _open_strong_read(path, label=label)
        self.identity = _stat_identity(os.fstat(self.descriptor))
        self.raw = _read_bounded_fd(
            self.descriptor,
            max_bytes=_MAX_STATE_BYTES,
            label=label,
        )
        self.verify()

    def verify(self) -> None:
        metadata = os.fstat(self.descriptor)
        raw = _read_bounded_fd(
            self.descriptor,
            max_bytes=_MAX_STATE_BYTES,
            label=self.label,
        )
        try:
            current = self.path.stat()
        except OSError as exc:
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} held path unavailable"
            ) from exc
        if (
            raw != self.raw
            or _stat_identity(metadata) != self.identity
            or _stat_identity(current) != self.identity
            or _is_reparse_point(self.path)
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                f"{self.label} held evidence changed"
            )

    def descriptor_value(self) -> dict[str, Any]:
        return {
            "path_sha256": hashlib.sha256(
                str(self.path.resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "sha256": hashlib.sha256(self.raw).hexdigest(),
            "size_bytes": len(self.raw),
        }

    def close(self) -> None:
        os.close(self.descriptor)


class _RunEvidenceClosure:
    def __init__(
        self,
        *,
        root: Path,
        spec: Mapping[str, Any],
    ) -> None:
        self.root = root
        self.spec = spec
        self.handles: list[_HeldRunEvidenceFile] = []
        try:
            spec_handle = self._open(root / "run-spec.json", "sealed run spec")
            if spec_handle.raw != _canonical_bytes(spec):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "sealed run spec drifted"
                )
            claim_handle = self._open(root / "claim.json", "sealed run claim")
            external_claim_handle = self._open(
                Path(spec["external_run_claim_path"]),
                "sealed external run claim",
            )
            if claim_handle.raw != external_claim_handle.raw:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "external run claim binding rejected"
                )
            self.claim = _validated_claim_value(
                _strict_json(
                    claim_handle.raw,
                    label="sealed run claim",
                    max_bytes=_MAX_STATE_BYTES,
                ),
                spec,
            )
            status_handle = self._open(root / "status.json", "sealed run status")
            self.status = _validated_status(
                _strict_json(
                    status_handle.raw,
                    label="sealed run status",
                    max_bytes=_MAX_STATE_BYTES,
                ),
                spec,
            )
            if (
                self.status["status"] != "completed"
                or self.status["claim_sha256"] != self.claim["claim_sha256"]
            ):
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "sealed run terminal binding rejected"
                )
            descriptor = self.status["receipt"]
            if type(descriptor) is not dict or set(descriptor) != {
                "file_sha256",
                "relative_path",
                "root_sha256",
            }:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "sealed run receipt descriptor rejected"
                )
            file_sha = _strict_sha256(
                descriptor["file_sha256"],
                label="sealed run receipt",
            )
            expected_relative = f"receipts/sha256/{file_sha[:2]}/{file_sha}.json"
            if descriptor["relative_path"] != expected_relative:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "sealed run receipt path rejected"
                )
            receipt_handle = self._open(
                root / Path(*expected_relative.split("/")),
                "sealed run receipt",
            )
            if hashlib.sha256(receipt_handle.raw).hexdigest() != file_sha:
                raise FactorV3ParentSourceAuthorityRunnerError(
                    "sealed run receipt content address rejected"
                )
            self.receipt = _validated_receipt(
                _strict_json(
                    receipt_handle.raw,
                    label="sealed run receipt",
                    max_bytes=_MAX_STATE_BYTES,
                ),
                spec=spec,
                status=self.status,
                claim=self.claim,
            )
            _assert_terminal_run_tree(root, descriptor, category="receipts")
            self.attestation = self._attestation()
        except BaseException:
            self.close()
            raise

    def _open(self, path: Path, label: str) -> _HeldRunEvidenceFile:
        handle = _HeldRunEvidenceFile(path, label=label)
        self.handles.append(handle)
        return handle

    def _attestation(self) -> dict[str, Any]:
        files = {
            handle.label: handle.descriptor_value()
            for handle in sorted(self.handles, key=lambda item: item.label)
        }
        unsigned = {
            "files": files,
            "run_candidate_root_sha256": self.receipt["candidate_root_sha256"],
            "run_claim_sha256": self.claim["claim_sha256"],
            "run_root_sha256": hashlib.sha256(
                str(self.root.resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "run_status_sha256": self.status["status_sha256"],
            "schema": "factor-v3-parent-source-sealed-run-evidence-attestation/v1",
        }
        return {**unsigned, "attestation_sha256": _canonical_sha256(unsigned)}

    def verify(self) -> dict[str, Any]:
        for handle in self.handles:
            handle.verify()
        _assert_terminal_run_tree(
            self.root,
            self.status["receipt"],
            category="receipts",
        )
        current = self._attestation()
        if current != self.attestation:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "sealed run evidence postverification changed"
            )
        return current

    def close(self) -> None:
        while self.handles:
            self.handles.pop().close()


def _initialize_verification(
    root: Path,
    spec: Mapping[str, Any],
    evidence: _RunEvidenceClosure,
) -> tuple[str, _RunOwnership]:
    if root != Path(spec["verification_root"]):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "canonical verification root binding rejected"
        )
    if any(root.iterdir()):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "single attempt verification root already claimed"
        )
    unsigned_claim = {
        "development_only": True,
        "external_claim_path_sha256": hashlib.sha256(
            spec["external_verification_claim_path"].encode("utf-8")
        ).hexdigest(),
        "formal_materialization_eligible": False,
        "run_candidate_root_sha256": evidence.receipt["candidate_root_sha256"],
        "run_claim_sha256": evidence.claim["claim_sha256"],
        "run_spec_sha256": spec["run_spec_sha256"],
        "run_status_sha256": evidence.status["status_sha256"],
        "schema": VERIFICATION_CLAIM_SCHEMA,
        "single_attempt": True,
        "verification_root_sha256": hashlib.sha256(
            spec["verification_root"].encode("utf-8")
        ).hexdigest(),
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    claim = {**unsigned_claim, "claim_sha256": _canonical_sha256(unsigned_claim)}
    raw = _canonical_bytes(claim)
    reservation_unsigned = {
        "claim_sha256": claim["claim_sha256"],
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": "factor-v3-parent-source-verification-status-reservation/v1",
        "state": "reserved",
    }
    reservation = {
        **reservation_unsigned,
        "reservation_sha256": _canonical_sha256(reservation_unsigned),
    }
    ownership = _RunOwnership()
    status_path = root / "status.json"
    external_claim_acquired = False
    try:
        ownership.status = ownership.add(
            _OwnedFile(
                status_path,
                label="verification status reservation",
                raw=_canonical_bytes(reservation),
            )
        )
        ownership.add(
            _OwnedFile(
                Path(spec["external_verification_claim_path"]),
                label="external verification claim",
                raw=raw,
            )
        )
        external_claim_acquired = True
        ownership.add(
            _OwnedFile(
                root / "claim.json",
                label="verification claim",
                raw=raw,
            )
        )
        ownership.verify()
    except BaseException as exc:
        if external_claim_acquired and ownership.status is not None:
            try:
                failure = _verification_failure(
                    spec=spec,
                    claim_sha256=claim["claim_sha256"],
                    stage="initialize-verification",
                    error=exc,
                    evidence=evidence,
                )
                relative, file_sha, failure_handle = _create_owned_cas(
                    root,
                    "verification-failures",
                    failure,
                )
                ownership.add(failure_handle)
                descriptor = _descriptor(
                    relative,
                    file_sha,
                    failure["failure_root_sha256"],
                )
                status = _verification_status(
                    spec=spec,
                    claim_sha256=claim["claim_sha256"],
                    status="failed",
                    receipt=None,
                    failure=descriptor,
                )
                ownership.status.rewrite_terminal(_canonical_bytes(status))
                ownership.verify()
            except BaseException:
                pass
        ownership.close()
        if not external_claim_acquired:
            status_path.unlink(missing_ok=True)
        raise
    return claim["claim_sha256"], ownership


def _independent_child_replay(
    *,
    spec: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    closure: _InputClosure | None = None
    stdout_handle: _OwnedFile | None = None
    stderr_handle: _OwnedFile | None = None
    exit_code: int | None = None
    stdout_descriptor: dict[str, Any] | None = None
    stderr_descriptor: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    postflight: dict[str, Any] | None = None
    try:
        closure = _InputClosure(spec)
        preflight = closure.preflight()
        stdout_handle = _open_child_log(root, ".verify-child-stdout.partial")
        stderr_handle = _open_child_log(root, ".verify-child-stderr.partial")
        try:
            exit_code = _spawn_child(
                spec,
                app_sources=closure.app_sources,
                runtime_manifest=closure.runtime_manifest,
                run_root=root,
                stdout_handle=stdout_handle,
                stderr_handle=stderr_handle,
            )
        except _ChildBoundaryError as exc:
            exit_code = exc.child_exit_code
            stdout_descriptor = exc.child_stdout
            stderr_descriptor = exc.child_stderr
            raise
        stdout = _read_child_log(
            stdout_handle,
            max_bytes=spec["subprocess"]["max_stdout_bytes"],
        )
        stderr = _read_child_log(
            stderr_handle,
            max_bytes=spec["subprocess"]["max_stderr_bytes"],
        )
        stdout_descriptor = _io_descriptor(stdout)
        stderr_descriptor = _io_descriptor(stderr)
        postflight = closure.postflight()
        if postflight != preflight:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "independent child postflight changed"
            )
        if exit_code != 0 or stderr:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "independent child verifier exit rejected"
            )
        child_result = _validate_child_result(stdout, spec)
        return {
            "child_result": child_result,
            "child_stderr": stderr_descriptor,
            "child_stdout": stdout_descriptor,
            "postflight": postflight,
            "preflight": preflight,
        }
    except BaseException as exc:
        if stdout_handle is not None and stdout_descriptor is None:
            try:
                stdout_descriptor = _io_descriptor(
                    _read_child_log(
                        stdout_handle,
                        max_bytes=spec["subprocess"]["max_stdout_bytes"],
                    )
                )
            except BaseException:
                stdout_descriptor = None
        if stderr_handle is not None and stderr_descriptor is None:
            try:
                stderr_descriptor = _io_descriptor(
                    _read_child_log(
                        stderr_handle,
                        max_bytes=spec["subprocess"]["max_stderr_bytes"],
                    )
                )
            except BaseException:
                stderr_descriptor = None
        if closure is not None and preflight is not None and postflight is None:
            try:
                postflight = closure.postflight()
            except BaseException:
                postflight = None
        raise _IndependentReplayError(
            str(exc),
            child_exit_code=exit_code,
            child_stdout=stdout_descriptor,
            child_stderr=stderr_descriptor,
            preflight=preflight,
            postflight=postflight,
        ) from exc
    finally:
        if closure is not None:
            closure.close()
        for handle in (stdout_handle, stderr_handle):
            if handle is not None:
                name = handle.path
                try:
                    handle.close()
                finally:
                    name.unlink(missing_ok=True)


def _verification_receipt(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    evidence: _RunEvidenceClosure,
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "candidate_verified": True,
        "child_result_sha256": replay["child_result"]["result_sha256"],
        "child_stderr": dict(replay["child_stderr"]),
        "child_stdout": dict(replay["child_stdout"]),
        "claim_sha256": claim_sha256,
        "development_only": True,
        "formal_materialization_eligible": False,
        "independent_public_replay_performed": True,
        "replay_postflight_attestation_sha256": replay["postflight"][
            "attestation_sha256"
        ],
        "replay_preflight_attestation_sha256": replay["preflight"][
            "attestation_sha256"
        ],
        "run_candidate_root_sha256": evidence.receipt["candidate_root_sha256"],
        "run_evidence_attestation_sha256": evidence.attestation[
            "attestation_sha256"
        ],
        "run_spec_sha256": spec["run_spec_sha256"],
        "run_status_sha256": evidence.status["status_sha256"],
        "schema": VERIFICATION_RECEIPT_SCHEMA,
        "source_authority_verified": False,
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "verification_root_sha256": _canonical_sha256(unsigned)}


def _verification_status(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    status: str,
    receipt: Mapping[str, Any] | None,
    failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise FactorV3ParentSourceAuthorityRunnerError(
            "verification status transition rejected"
        )
    unsigned = {
        "candidate_verified": status == "completed",
        "claim_sha256": claim_sha256,
        "development_only": True,
        "failure": None if failure is None else dict(failure),
        "formal_materialization_eligible": False,
        "independent_public_replay_performed": status == "completed",
        "receipt": None if receipt is None else dict(receipt),
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": VERIFICATION_STATUS_SCHEMA,
        "source_authority_verified": False,
        "status": status,
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "status_sha256": _canonical_sha256(unsigned)}


def _verification_failure(
    *,
    spec: Mapping[str, Any],
    claim_sha256: str,
    stage: str,
    error: BaseException,
    evidence: _RunEvidenceClosure,
) -> dict[str, Any]:
    replay_error = error if isinstance(error, _IndependentReplayError) else None
    unsigned = {
        "child_exit_code": (
            None if replay_error is None else replay_error.child_exit_code
        ),
        "child_stderr": (
            None if replay_error is None else replay_error.child_stderr
        ),
        "child_stdout": (
            None if replay_error is None else replay_error.child_stdout
        ),
        "claim_sha256": claim_sha256,
        "development_only": True,
        "error_type": type(error).__name__,
        "formal_materialization_eligible": False,
        "run_candidate_root_sha256": evidence.receipt["candidate_root_sha256"],
        "run_evidence_attestation_sha256": evidence.attestation[
            "attestation_sha256"
        ],
        "run_spec_sha256": spec["run_spec_sha256"],
        "replay_postflight_attestation_sha256": (
            None
            if replay_error is None or replay_error.postflight is None
            else replay_error.postflight["attestation_sha256"]
        ),
        "replay_preflight_attestation_sha256": (
            None
            if replay_error is None or replay_error.preflight is None
            else replay_error.preflight["attestation_sha256"]
        ),
        "schema": VERIFICATION_FAILURE_SCHEMA,
        "source_authority_verified": False,
        "stage": stage,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return {**unsigned, "failure_root_sha256": _canonical_sha256(unsigned)}


def _verification_result(
    *,
    spec: Mapping[str, Any],
    evidence: _RunEvidenceClosure,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_verified": True,
        "development_only": True,
        "formal_materialization_eligible": False,
        "independent_public_replay_performed": True,
        "run_candidate_root_sha256": evidence.receipt["candidate_root_sha256"],
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": VERIFICATION_RESULT_SCHEMA,
        "source_authority_verified": False,
        "status": "completed",
        "verification_receipt_relative_path": descriptor["relative_path"],
        "verification_receipt_sha256": descriptor["file_sha256"],
        "verification_root_sha256": descriptor["root_sha256"],
        "verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }


def verify_factor_v3_parent_source_candidate_run(
    *,
    run_spec_path: str | Path,
    run_root: str | Path,
    expected_run_spec_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Independently replay into a separate, single-attempt verification root."""

    spec = load_factor_v3_parent_source_candidate_run_spec(
        Path(run_spec_path),
        expected_file_sha256=expected_run_spec_file_sha256,
    )
    requested_run_root = Path(run_root).resolve(strict=False)
    if requested_run_root != Path(spec["run_root"]):
        raise FactorV3ParentSourceAuthorityRunnerError(
            "canonical run root binding rejected"
        )
    root = _run_root(requested_run_root, create=False)
    _assert_disjoint(root, spec)
    evidence: _RunEvidenceClosure | None = None
    ownership: _RunOwnership | None = None
    verification_root: Path | None = None
    claim_sha256 = ""
    stage = "sealed-run-evidence"
    try:
        evidence = _RunEvidenceClosure(root=root, spec=spec)
        verification_root = _run_root(spec["verification_root"], create=True)
        _assert_disjoint(
            verification_root,
            spec,
            verification_root_is_output=True,
        )
        claim_sha256, ownership = _initialize_verification(
            verification_root,
            spec,
            evidence,
        )
        stage = "independent-replay"
        replay = _independent_child_replay(spec=spec, root=verification_root)
        if (
            evidence.receipt["preflight_attestation_sha256"]
            != replay["preflight"]["attestation_sha256"]
            or evidence.receipt["postflight_attestation_sha256"]
            != replay["postflight"]["attestation_sha256"]
            or evidence.receipt["child_result_sha256"]
            != replay["child_result"]["result_sha256"]
            or evidence.receipt["child_stdout"] != replay["child_stdout"]
            or evidence.receipt["child_stderr"] != replay["child_stderr"]
        ):
            raise FactorV3ParentSourceAuthorityRunnerError(
                "independent public replay drifted"
            )
        stage = "publish-verification"
        receipt = _verification_receipt(
            spec=spec,
            claim_sha256=claim_sha256,
            evidence=evidence,
            replay=replay,
        )
        relative, file_sha, receipt_handle = _create_owned_cas(
            verification_root,
            "verification-receipts",
            receipt,
        )
        ownership.add(receipt_handle)
        descriptor = _descriptor(
            relative,
            file_sha,
            receipt["verification_root_sha256"],
        )
        status = _verification_status(
            spec=spec,
            claim_sha256=claim_sha256,
            status="completed",
            receipt=descriptor,
            failure=None,
        )
        if ownership.status is None:
            raise FactorV3ParentSourceAuthorityRunnerError(
                "verification status reservation unavailable"
            )
        ownership.status.rewrite_terminal(_canonical_bytes(status))
        evidence.verify()
        ownership.verify()
        _assert_terminal_output_tree(
            verification_root,
            descriptor,
            category="verification-receipts",
            expected_top_files={"claim.json", "status.json"},
        )
        return _verification_result(
            spec=spec,
            evidence=evidence,
            descriptor=descriptor,
        )
    except BaseException as exc:
        if ownership is not None and verification_root is not None and evidence is not None:
            try:
                failure = _verification_failure(
                    spec=spec,
                    claim_sha256=claim_sha256,
                    stage=stage,
                    error=exc,
                    evidence=evidence,
                )
                relative, file_sha, failure_handle = _create_owned_cas(
                    verification_root,
                    "verification-failures",
                    failure,
                )
                ownership.add(failure_handle)
                descriptor = _descriptor(
                    relative,
                    file_sha,
                    failure["failure_root_sha256"],
                )
                status = _verification_status(
                    spec=spec,
                    claim_sha256=claim_sha256,
                    status="failed",
                    receipt=None,
                    failure=descriptor,
                )
                if ownership.status is not None:
                    ownership.status.rewrite_terminal(_canonical_bytes(status))
                evidence.verify()
                ownership.verify()
                _assert_terminal_output_tree(
                    verification_root,
                    descriptor,
                    category="verification-failures",
                    expected_top_files={"claim.json", "status.json"},
                )
            except BaseException:
                pass
        if isinstance(exc, FactorV3ParentSourceAuthorityRunnerError):
            raise
        raise FactorV3ParentSourceAuthorityRunnerError(
            "parent source independent verification failed"
        ) from exc
    finally:
        if evidence is not None:
            evidence.close()
        if ownership is not None:
            ownership.close()


def _load_failure_for_test(run_root: str | Path) -> dict[str, Any]:
    root = _run_root(run_root, create=False)
    status_value, _raw = _read_json(root / "status.json", label="run status", max_bytes=_MAX_STATE_BYTES)
    failure_descriptor = status_value.get("failure")
    return _load_cas(root, failure_descriptor, category="failures")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factor-v3-parent-source-candidate-runner")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--run-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            result = run_factor_v3_parent_source_candidate(
                run_spec_path=args.run_spec,
                run_root=args.run_root,
            )
        else:
            result = verify_factor_v3_parent_source_candidate_run(
                run_spec_path=args.run_spec,
                run_root=args.run_root,
            )
    except (FactorV3ParentSourceAuthorityRunnerError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(_canonical_bytes(result).decode("utf-8"))
    return 0


__all__ = (
    "CHILD_RESULT_SCHEMA",
    "FACTOR_V2_SPEC_SHA256",
    "FROZEN_SOURCE_BLOB_SHA256",
    "FROZEN_SOURCE_COMMIT",
    "FROZEN_SOURCE_TREE_OID",
    "FactorV3ParentSourceAuthorityRunnerError",
    "OVERLAY_ARTIFACT_SHA256",
    "OVERLAY_DATABASE_FILENAME",
    "OVERLAY_MANIFEST_FILE_SHA256",
    "PARENT_ARTIFACT_SHA256",
    "PARENT_DATABASE_FILENAMES",
    "PARENT_MANIFEST_FILE_SHA256",
    "RUN_SPEC_SCHEMA",
    "RUNTIME_DEPENDENCY_MANIFEST_SCHEMA",
    "SAFETY_FALSE_FIELDS",
    "SUSPENSION_BUNDLE_SHA256",
    "SUSPENSION_METADATA_SHA256",
    "VERIFICATION_RESULT_SCHEMA",
    "build_factor_v3_runtime_dependency_manifest",
    "build_factor_v3_parent_source_candidate_run_spec",
    "load_factor_v3_parent_source_candidate_run_spec",
    "main",
    "run_factor_v3_parent_source_candidate",
    "verify_factor_v3_parent_source_candidate_run",
)


if __name__ == "__main__":
    raise SystemExit(main())
