from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import msvcrt
except ImportError:  # pragma: no cover - formal execution is Windows-only
    msvcrt = None


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[1]
RUN_ROOT_RELATIVE = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_risk_on_breadth_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
ATTEMPT_RELATIVE = Path(
    "data/research_attempts/"
    "risk_on_breadth_development_1.formal_attempt.json"
)
TERMINAL_RELATIVE = Path(
    "data/research_attempts/"
    "risk_on_breadth_development_1.formal_attempt.terminal.json"
)
SOURCE_AUTHORITY_RELATIVE_ROOT = Path(
    "docs/research_preregistrations/"
    "shallow_gbdt_risk_on_breadth_development_1_source_authority_v1"
)
VERIFIER_AMENDMENT_RELATIVE_ROOT = Path(
    "docs/research_preregistrations/"
    "shallow_gbdt_risk_on_breadth_development_1_"
    "post_run_verifier_amendment_authority_v1"
)
VERIFIER_AMENDMENT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "post-run-verifier-amendment-authority/v1"
)
VERIFIER_AMENDMENT_GIT_ATTRIBUTES_RULE = (
    "docs/research_preregistrations/"
    "shallow_gbdt_risk_on_breadth_development_1_"
    "post_run_verifier_amendment_authority_v1/*.json -text"
)
VERIFIER_AMENDMENT_VERIFIER_GIT_PATH = (
    "scripts/verify_shallow_gbdt_risk_on_breadth_development_1.py"
)
VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS = (
    ".gitattributes",
    VERIFIER_AMENDMENT_VERIFIER_GIT_PATH,
    "tests/test_shallow_gbdt_risk_on_breadth_independent_verifier.py",
    "tests/test_shallow_gbdt_risk_on_breadth_formal_launcher.py",
)
RETRY_AUTHORITY_RELATIVE_ROOT = Path(
    "docs/research_preregistrations/"
    "shallow_gbdt_risk_on_breadth_development_1_"
    "post_failure_verifier_retry_authority_v1"
)
RETRY_AUTHORITY_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "post-failure-verifier-retry-authority/v1"
)
RETRY_AUTHORITY_GIT_ATTRIBUTES_RULE = (
    "docs/research_preregistrations/"
    "shallow_gbdt_risk_on_breadth_development_1_"
    "post_failure_verifier_retry_authority_v1/*.json -text"
)
RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS = (
    ".gitattributes",
    VERIFIER_AMENDMENT_VERIFIER_GIT_PATH,
    "tests/test_shallow_gbdt_risk_on_breadth_independent_verifier.py",
)
EXPECTED_RUN_SPEC_SHA256 = (
    "d27c352ff362710ecdbf58791a5aa95b25aae75a2a25e0c351b7901b26212471"
)
EXPECTED_PYCACHE_BLOCKER_SHA256 = (
    "78c73250a8d2c984878b6dbc5e7775a261be07d81e36af54a0fad8c926d98576"
)
PYCACHE_BLOCKER_RELATIVE = Path("scripts/formal_pycache_blocker_v1")
COMPLETION_NAME = "formal_run.completion.json"
PREFLIGHT_NAME = "formal_run.preflight.json"
LAUNCH_NAME = "formal_run.launch.json"
RESOURCE_NAME = "formal_run.resource_receipt.json"
FORMAL_STDOUT_NAME = "formal_run.stdout.log"
FORMAL_STDERR_NAME = "formal_run.stderr.log"
FAILURE_NAME = "formal_run.failure.json"
CLAIM_NAME = "formal_run.risk_on_breadth_independent_verification.claim.json"
STATUS_NAME = "formal_run.risk_on_breadth_independent_verification.status.json"
VERIFICATION_ROOT_NAME = "risk_on_breadth_independent_verifications"
RECEIPT_ROOT_NAME = "risk_on_breadth_independent_verification_receipts"
REPLAY_STDOUT_NAME = "risk_on_breadth_independent_replay.stdout.log"
REPLAY_STDERR_NAME = "risk_on_breadth_independent_replay.stderr.log"
RETRY_CLAIM_NAME = (
    "formal_run.risk_on_breadth_independent_verification.retry_1.claim.json"
)
RETRY_STATUS_NAME = (
    "formal_run.risk_on_breadth_independent_verification.retry_1.status.json"
)
RETRY_VERIFICATION_ROOT_NAME = "risk_on_breadth_independent_verifications_retry_1"
RETRY_RECEIPT_ROOT_NAME = (
    "risk_on_breadth_independent_verification_receipts_retry_1"
)
RETRY_FAILURE_RECEIPT_ROOT_NAME = (
    "risk_on_breadth_independent_verification_failure_receipts_retry_1"
)
RETRY_REPLAY_STDOUT_NAME = (
    "risk_on_breadth_independent_replay.retry_1.stdout.log"
)
RETRY_REPLAY_STDERR_NAME = (
    "risk_on_breadth_independent_replay.retry_1.stderr.log"
)
EXPECTED_FAILED_CLAIM_SHA256 = (
    "bbfa49f223b60385716d4d4179955a14e24c23dea5914e4c83b18339187e69f1"
)
EXPECTED_FAILED_STATUS_SHA256 = (
    "98be05575651c0327edb49db3e65be838656c763cecf681866f3c59ec901a38f"
)
ORIGINAL_FAILED_CLAIM_FIELDS = frozenset(
    {
        "schema_version",
        "pid",
        "completion_sha256",
        "replay_plan_sha256",
        "verifier_amendment_sha256",
        "successor_verifier_git_blob_sha256",
        "json_document_size_policy_sha256",
        "development_only",
        "embargo_consumed",
        "final_oos_consumed",
        "production_authority",
        "automatic_trading_authority",
    }
)
ORIGINAL_FAILED_STATUS_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "stage",
        "verified",
        "receipt_sha256",
        "error_type",
        "point_in_time",
        "development_only",
        "development_statistical_interpretation_allowed",
        "profile_registration_authority",
        "production_recommendation_authority",
        "automatic_trading_authority",
        "production_authority",
        "embargo_consumed",
        "final_oos_consumed",
        "claim_sha256",
        "completion_sha256",
        "verifier_amendment_sha256",
        "successor_verifier_git_blob_sha256",
        "json_document_size_policy_sha256",
        "receipt_path",
    }
)
HEX_GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_STRATEGY_SHA256 = (
    "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "bb833d0bc91720b3bf46b204ffb4d8615a9ec4530b7734d48ee3663bcd1d753f"
)
EXPECTED_PRODUCER_SCHEMA = (
    "audited-pit-ranked-liquidity-shallow-gbdt-risk-on-breadth-producer/v1"
)
EXPECTED_RESULT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-result/v1"
)
EXPECTED_VERIFICATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "result-bundle-verification/v1"
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
        "market_breadth_filter_replayed",
    }
)
RESULT_BUNDLE_SIDECAR_SCHEMAS = {
    name: (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        f"{name}-sidecar/v1"
    )
    for name in ("features", "models", "execution", "selection")
}
PRODUCER_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "base_shallow_gbdt_producer_root_sha256",
        "shared_ranked_liquidity_module_sha256",
        "risk_on_breadth_module_sha256",
        "config_module_sha256",
        "formal_dispatch_module_sha256",
        "risk_on_breadth_strategy_sha256",
        "root_sha256",
    }
)
EXPECTED_RESULT_SCOPE = {
    "point_in_time": True,
    "development_only": True,
    "strict_artifact_native_execution": True,
    "intraday_fill_claimed": False,
    "embargo_consumed": False,
    "final_oos_consumed": False,
    "eligible_for_profile_registration": False,
    "production_recommendation_eligible": False,
}
EXPECTED_MARKET_BREADTH_GATE = {
    "feature": "cross_section_above_ma20_fraction",
    "comparison": "greater_than_or_equal_unrounded_float64",
    "minimum": 0.5,
    "missing_policy": "fail_closed_run",
}
MARKET_BREADTH_FILTER_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "filter",
        "input_candidate_count",
        "input_candidate_keys_sha256",
        "eligible_candidate_count",
        "eligible_candidate_keys_sha256",
        "excluded_candidate_count",
        "excluded_candidate_keys_sha256",
        "receipt_sha256",
    }
)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
JSON_DOCUMENT_SIZE_POLICY: dict[str, Any] = {
    "schema_version": (
        "risk-on-breadth-independent-json-document-size-policy/v1"
    ),
    "limits_bytes": {
        "formal_control": 64 * 1024 * 1024,
        "runtime_verification": 64 * 1024 * 1024,
        "result_main": 64 * 1024 * 1024,
        "result_sidecars": {
            "features": 64 * 1024 * 1024,
            "models": 64 * 1024 * 1024,
            "execution": 512 * 1024 * 1024,
            "selection": 768 * 1024 * 1024,
        },
    },
}
EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256 = (
    "e30af910f48cfb5a95f68cda2defd64597af841cf5911d0b628500105538e216"
)
VERIFIER_AMENDMENT_SCOPE = {
    "point_in_time": True,
    "development_only": True,
    "formal_completion_reverification_only": True,
    "development_statistical_interpretation_allowed": False,
    "profile_registration_authority": False,
    "production_recommendation_authority": False,
    "automatic_trading_authority": False,
    "production_authority": False,
    "embargo_consumed": False,
    "final_oos_consumed": False,
}
VERIFIER_AMENDMENT_TOPOLOGY = {
    "successor_source_parent_is_formal_execution": True,
    "successor_source_change": "modify_exact_successor_git_blobs_only",
    "amendment_execution_parent_is_successor_source": True,
    "amendment_execution_change": (
        "add_single_verifier_amendment_authority_only"
    ),
}
EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT = (
    "e2bb5ea6e8f5799d3145b1b6cd936c39339ae12b"
)
EXPECTED_VERIFIER_AMENDMENT_ARTIFACT_SHA256 = (
    "76a92ac11c73f2e9f2241a04c8ec296c049e3a8b15830ec10476ef3a3b497f07"
)
RETRY_AUTHORITY_SCOPE = {
    "point_in_time": True,
    "development_only": True,
    "post_failure_verification_retry_only": True,
    "maximum_retry_attempts": 1,
    "development_statistical_interpretation_allowed": False,
    "profile_registration_authority": False,
    "production_recommendation_authority": False,
    "automatic_trading_authority": False,
    "production_authority": False,
    "embargo_consumed": False,
    "final_oos_consumed": False,
}
RETRY_FAILURE_CLASSIFICATION = {
    "stage": "isolated_replay_bootstrap",
    "before": "_load_ranked_liquidity_bars",
    "child_exit_code": 7,
    "error_chain": [
        "AuditedPITDevelopmentReplayError",
        "PITReceiptError",
    ],
    "root_cause_code": "incomplete_audited_pit_bundle_copy",
}
RETRY_AUTHORITY_TOPOLOGY = {
    "retry_source_parent_is_original_amendment_execution": True,
    "retry_source_change": "modify_exact_retry_git_blobs_only",
    "retry_authority_execution_parent_is_retry_source": True,
    "retry_authority_execution_change": (
        "add_single_post_failure_retry_authority_only"
    ),
}
RECEIPT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-receipt/v2"
)
STATUS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-status/v2"
)
RETRY_RECEIPT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-retry-receipt/v1"
)
RETRY_FAILURE_RECEIPT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-retry-failure-receipt/v1"
)
RETRY_STATUS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-retry-status/v1"
)
REPLAY_RESULT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-replay-result/v1"
)
EXECUTION_SNAPSHOT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-execution-snapshot/v1"
)
RELOCATION_EQUIVALENCE_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "relocation-equivalence/v1"
)
FORMAL_RUNTIME_HISTORY_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "formal-runtime-history/v1"
)
NORMALIZED_XGBOOST_LIBRARY = "<relocated-xgboost-library>"
REPLAY_PLAN: dict[str, Any] = {
    "schema_version": (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        "independent-replay-plan/v1"
    ),
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
        "security_code_transition_evidence_root": (
            "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
        "current_pool_development_audit_path": (
            "data/research_artifacts/current_pool_audits/"
            "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55.json"
        ),
        "expected_current_pool_development_audit_sha256": (
            "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55"
        ),
    },
    "development_partition": {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    },
    "execution": {
        "entrypoint": "app.jobs",
        "direct_callable_allowed": False,
    },
    "scope": {
        "point_in_time": True,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    },
}
EXPECTED_REPLAY_PLAN_SHA256 = (
    "c9df60391347c24903053d9ead13777188f052f845e167a365c0e42c92538043"
)
ISOLATED_REPLAY_DRIVER = r'''
import json
from pathlib import Path
import runpy
import sys

argv = json.loads(sys.argv[1])
if not isinstance(argv, list) or argv[:2] != ["-m", "app.jobs"]:
    raise SystemExit("isolated replay argv is invalid")
code_root = str(Path(sys.argv[2]).resolve(strict=True))
site_packages = str(Path(sys.argv[3]).resolve(strict=True))
sys.path[:0] = [code_root, site_packages]
sys.argv[:] = ["app.jobs", *argv[2:]]
runpy.run_module("app.jobs", run_name="__main__", alter_sys=True)
'''
FORMAL_APP_JOBS_DRIVER = r'''
import json
import os
import runpy
import sys

if (sys.flags.no_site != 1 or sys.flags.safe_path != 1
        or sys.flags.ignore_environment != 0
        or sys.flags.dont_write_bytecode != 1
        or os.environ.get("PYTHONHASHSEED") != "0"):
    raise SystemExit(65)
sys.path[:0] = json.loads(sys.argv.pop(1))
if sys.argv[1:3] != ["-m", "app.jobs"]:
    raise SystemExit(64)
sys.argv = ["app.jobs", *sys.argv[3:]]
runpy.run_module("app.jobs", run_name="__main__", alter_sys=True)
'''


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _assert_frozen_replay_plan() -> None:
    if _sha256(REPLAY_PLAN) != EXPECTED_REPLAY_PLAN_SHA256:
        raise ValueError("independent replay plan drifted")


def _require_sha256(value: object, label: str) -> str:
    observed = str(value or "")
    if not HEX_SHA256.fullmatch(observed):
        raise ValueError(f"{label} is not a SHA-256")
    return observed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains duplicate keys")
        result[key] = value
    return result


def _assert_no_reparse(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
        raise ValueError(f"{label} must not be a reparse point")
    return metadata


def _json_document_size_limit(document_class: str) -> int:
    if _sha256(JSON_DOCUMENT_SIZE_POLICY) != (
        EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
    ):
        raise ValueError("JSON document size policy drifted")
    limits = JSON_DOCUMENT_SIZE_POLICY["limits_bytes"]
    if document_class in {
        "formal_control",
        "runtime_verification",
        "result_main",
    }:
        value = limits[document_class]
    elif document_class.startswith("result_sidecar_"):
        name = document_class.removeprefix("result_sidecar_")
        sidecars = limits["result_sidecars"]
        if name not in RESULT_BUNDLE_SIDECAR_SCHEMAS:
            raise ValueError("JSON document class is invalid")
        value = sidecars[name]
    else:
        raise ValueError("JSON document class is invalid")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("JSON document size policy is invalid")
    return value


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_dev),
        int(metadata.st_ino),
    )


def _stable_object_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (
        int(metadata.st_mode),
        int(metadata.st_dev),
        int(metadata.st_ino),
    )


def _owned_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_ctime_ns", 0)),
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(getattr(metadata, "st_nlink", 0)),
    )


def _read_json_bytes(
    path: Path,
    label: str,
    *,
    document_class: str,
) -> tuple[dict[str, Any], bytes]:
    limit = _json_document_size_limit(document_class)
    metadata_before = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata_before.st_mode) or metadata_before.st_size > limit:
        raise ValueError(f"{label} is not a bounded regular file")
    try:
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if _metadata_identity(opened_before) != _metadata_identity(
                metadata_before
            ):
                raise ValueError(f"{label} is not a bounded regular file")
            raw = handle.read(limit + 1)
            opened_after = os.fstat(handle.fileno())
        metadata_after = _assert_no_reparse(path, label)
        if (
            len(raw) > limit
            or len(raw) != opened_before.st_size
            or _metadata_identity(opened_after)
            != _metadata_identity(opened_before)
            or _metadata_identity(metadata_after)
            != _metadata_identity(metadata_before)
        ):
            raise ValueError(f"{label} is not a bounded regular file")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value, raw


def _read_json_object(
    path: Path,
    label: str,
    *,
    document_class: str,
) -> dict[str, Any]:
    value, _raw = _read_json_bytes(
        path,
        label,
        document_class=document_class,
    )
    return value


def _content_addressed_document(
    path: Path,
    label: str,
    *,
    document_class: str,
) -> dict[str, Any]:
    document = _read_json_object(
        path,
        label,
        document_class=document_class,
    )
    unsigned = dict(document)
    embedded = _require_sha256(unsigned.pop("artifact_sha256", None), label)
    canonical = _sha256(unsigned)
    if embedded != canonical or path.name != f"{canonical}.json":
        raise ValueError(f"{label} is not content addressed")
    return document


def _directory_entries(directory: Path, label: str) -> list[Path]:
    metadata = _assert_no_reparse(directory, label)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} is not a directory")
    try:
        return list(directory.iterdir())
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc


def _verified_producer_binding(
    value: object,
    *,
    expected_producer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PRODUCER_BINDING_FIELDS:
        raise ValueError("result bundle producer binding fields drifted")
    producer = dict(value)
    root_sha256 = _require_sha256(
        producer.pop("root_sha256"),
        "result bundle producer root",
    )
    for field in PRODUCER_BINDING_FIELDS - {"schema_version", "root_sha256"}:
        _require_sha256(producer.get(field), f"result bundle producer {field}")
    verified = {**producer, "root_sha256": root_sha256}
    if expected_producer_binding is None:
        expected_root_sha256 = EXPECTED_PRODUCER_ROOT_SHA256
    else:
        expected, expected_root_sha256 = _verified_risk_binding(
            expected_producer_binding,
            "expected result bundle producer",
        )
        if verified != expected:
            raise ValueError("result bundle producer binding differs")
    if (
        producer.get("schema_version") != EXPECTED_PRODUCER_SCHEMA
        or producer.get("risk_on_breadth_strategy_sha256")
        != EXPECTED_STRATEGY_SHA256
        or root_sha256 != expected_root_sha256
        or _sha256(producer) != root_sha256
    ):
        raise ValueError("result bundle producer binding is invalid")
    return verified


def _verified_strategy_binding(
    value: object,
    *,
    top_level_sha256: object,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("result bundle strategy binding is invalid")
    strategy = dict(value)
    nested_sha256 = _require_sha256(
        strategy.pop("strategy_sha256", None),
        "result bundle strategy",
    )
    if (
        top_level_sha256 != nested_sha256
        or nested_sha256 != EXPECTED_STRATEGY_SHA256
        or _sha256(strategy) != nested_sha256
    ):
        raise ValueError("result bundle strategy binding is invalid")
    return {**strategy, "strategy_sha256": nested_sha256}


def _verified_result_scope(value: object) -> dict[str, Any]:
    required_fields = {*EXPECTED_RESULT_SCOPE, "advancement_gate_passed"}
    if not isinstance(value, Mapping) or set(value) != required_fields:
        raise ValueError("result bundle scope fields drifted")
    scope = dict(value)
    if any(scope.get(field) is not expected for field, expected in EXPECTED_RESULT_SCOPE.items()):
        raise ValueError("result bundle scope is invalid")
    if not isinstance(scope.get("advancement_gate_passed"), bool):
        raise ValueError("result bundle advancement gate is invalid")
    return scope


def _verified_market_breadth_filter_receipt(value: object) -> str:
    if not isinstance(value, Mapping) or set(value) != MARKET_BREADTH_FILTER_RECEIPT_FIELDS:
        raise ValueError("result bundle breadth filter receipt fields drifted")
    receipt = dict(value)
    receipt_sha256 = _require_sha256(
        receipt.pop("receipt_sha256"),
        "result bundle breadth filter receipt",
    )
    counts = (
        receipt.get("input_candidate_count"),
        receipt.get("eligible_candidate_count"),
        receipt.get("excluded_candidate_count"),
    )
    for field in (
        "input_candidate_keys_sha256",
        "eligible_candidate_keys_sha256",
        "excluded_candidate_keys_sha256",
    ):
        _require_sha256(receipt.get(field), f"result bundle breadth filter {field}")
    if (
        receipt.get("schema_version")
        != "ranked-liquidity-market-breadth-filter-receipt/v1"
        or receipt.get("filter") != EXPECTED_MARKET_BREADTH_GATE
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in counts
        )
        or counts[0] != counts[1] + counts[2]
        or _sha256(receipt) != receipt_sha256
    ):
        raise ValueError("result bundle breadth filter receipt is invalid")
    return receipt_sha256


def _runtime_envelope(
    document: Mapping[str, Any],
    *,
    expected_main_sha256: str,
    expected_sidecar_sha256: Mapping[str, str],
    expected_producer_root_sha256: str | None = None,
) -> dict[str, Any]:
    expected_main = _require_sha256(
        expected_main_sha256,
        "runtime verification expected main artifact",
    )
    expected_sidecars = {
        name: _require_sha256(
            expected_sidecar_sha256.get(name),
            f"runtime verification expected {name} sidecar",
        )
        for name in RESULT_BUNDLE_SIDECAR_SCHEMAS
    }
    if set(expected_sidecar_sha256) != set(RESULT_BUNDLE_SIDECAR_SCHEMAS):
        raise ValueError("runtime verification expected sidecars drifted")
    expected_producer_root = _require_sha256(
        expected_producer_root_sha256 or EXPECTED_PRODUCER_ROOT_SHA256,
        "runtime verification expected producer root",
    )

    required_fields = {
        "schema_version",
        "strategy_sha256",
        "producer_root_sha256",
        "main_artifact_sha256",
        "sidecar_artifact_sha256",
        "checks",
        "market_breadth_feature_binding_receipt_sha256",
        "verified",
        "receipt_sha256",
    }
    observed_fields = set(document)
    if observed_fields not in (required_fields, required_fields | {"artifact_sha256"}):
        raise ValueError("runtime verification fields drifted")

    unsigned = dict(document)
    if "artifact_sha256" in unsigned:
        artifact_sha256 = _require_sha256(
            unsigned.pop("artifact_sha256"),
            "runtime verification artifact",
        )
        if artifact_sha256 != _sha256(unsigned):
            raise ValueError("runtime verification artifact hash is invalid")
    receipt_unsigned = dict(unsigned)
    receipt_sha256 = _require_sha256(
        receipt_unsigned.pop("receipt_sha256", None),
        "runtime verification receipt",
    )
    if receipt_sha256 != _sha256(receipt_unsigned):
        raise ValueError("runtime verification receipt hash is invalid")

    checks = document.get("checks")
    sidecars = document.get("sidecar_artifact_sha256")
    feature_receipt_sha256 = _require_sha256(
        document.get("market_breadth_feature_binding_receipt_sha256"),
        "runtime verification market breadth feature binding receipt",
    )
    if (
        document.get("schema_version") != EXPECTED_VERIFICATION_SCHEMA
        or document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or document.get("producer_root_sha256")
        != expected_producer_root
        or document.get("main_artifact_sha256") != expected_main
        or not isinstance(sidecars, Mapping)
        or dict(sidecars) != expected_sidecars
        or not isinstance(checks, Mapping)
        or set(checks) != set(REQUIRED_VERIFICATION_CHECKS)
        or any(value is not True for value in checks.values())
        or document.get("verified") is not True
    ):
        raise ValueError("runtime verification contract is invalid")
    return {
        **dict(document),
        "market_breadth_feature_binding_receipt_sha256": (
            feature_receipt_sha256
        ),
    }


def _jobs_argv(output_dir: str) -> list[str]:
    _assert_frozen_replay_plan()
    return [
        "-m",
        "app.jobs",
        REPLAY_PLAN["command"],
        "--audited-pit-universe-path",
        REPLAY_PLAN["inputs"]["audited_pit_universe_path"],
        "--expected-coverage-audit-sha256",
        REPLAY_PLAN["inputs"]["expected_coverage_audit_sha256"],
        "--expected-artifact-root-sha256",
        REPLAY_PLAN["inputs"]["expected_artifact_root_sha256"],
        "--temporal-contract-path",
        REPLAY_PLAN["inputs"]["temporal_contract_path"],
        "--expected-temporal-contract-sha256",
        REPLAY_PLAN["inputs"]["expected_temporal_contract_sha256"],
        "--security-code-transition-evidence-root",
        REPLAY_PLAN["inputs"]["security_code_transition_evidence_root"],
        "--expected-security-code-transition-contract-sha256",
        REPLAY_PLAN["inputs"][
            "expected_security_code_transition_contract_sha256"
        ],
        "--start-date",
        REPLAY_PLAN["development_partition"]["start_date"],
        "--end-date",
        REPLAY_PLAN["development_partition"]["end_date"],
        "--output-dir",
        str(output_dir),
    ]


def _verify_runtime_verification(
    run_root: Path,
    *,
    expected_main_sha256: str,
    expected_sidecar_sha256: Mapping[str, str],
    expected_producer_root_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        run_root_metadata = _assert_no_reparse(
            run_root,
            "runtime verification run root",
        )
        if not stat.S_ISDIR(run_root_metadata.st_mode):
            raise ValueError("runtime verification run root is not a directory")
        verification_dir = run_root / "verifications"
        entries = _directory_entries(
            verification_dir,
            "runtime verification directory",
        )
        if len(entries) != 1 or entries[0].suffix != ".json":
            raise ValueError("runtime verification artifact set is not exact")
        document = _content_addressed_document(
            entries[0],
            "runtime verification artifact",
            document_class="runtime_verification",
        )
        return _runtime_envelope(
            document,
            expected_main_sha256=expected_main_sha256,
            expected_sidecar_sha256=expected_sidecar_sha256,
            expected_producer_root_sha256=(
                expected_producer_root_sha256
            ),
        )
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "runtime verification"
        ):
            raise
        raise ValueError("runtime verification is invalid") from exc


def _load_content_addressed_result_bundle(
    run_root: Path,
    *,
    runtime_verification: Mapping[str, Any],
    expected_producer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        run_root_metadata = _assert_no_reparse(
            run_root,
            "result bundle run root",
        )
        if not stat.S_ISDIR(run_root_metadata.st_mode):
            raise ValueError("result bundle run root is not a directory")
        runtime_sidecars = runtime_verification.get(
            "sidecar_artifact_sha256"
        )
        if not isinstance(runtime_sidecars, Mapping):
            raise ValueError("result bundle runtime sidecars are invalid")
        sidecar_hashes = {
            name: _require_sha256(
                runtime_sidecars.get(name),
                f"result bundle {name} sidecar",
            )
            for name in RESULT_BUNDLE_SIDECAR_SCHEMAS
        }
        if set(runtime_sidecars) != set(RESULT_BUNDLE_SIDECAR_SCHEMAS):
            raise ValueError("result bundle runtime sidecars drifted")
        main_sha256 = _require_sha256(
            runtime_verification.get("main_artifact_sha256"),
            "result bundle main artifact",
        )
        if expected_producer_binding is None:
            expected_producer_root_sha256 = EXPECTED_PRODUCER_ROOT_SHA256
        else:
            (
                expected_producer,
                expected_producer_root_sha256,
            ) = _verified_risk_binding(
                expected_producer_binding,
                "expected result bundle producer",
            )
            if dict(expected_producer_binding) != expected_producer:
                raise ValueError("result bundle expected producer differs")
        runtime = _verify_runtime_verification(
            run_root,
            expected_main_sha256=main_sha256,
            expected_sidecar_sha256=sidecar_hashes,
            expected_producer_root_sha256=(
                expected_producer_root_sha256
            ),
        )
        if runtime != dict(runtime_verification):
            raise ValueError("result bundle runtime verification anchor differs")

        main_path = run_root / f"{main_sha256}.json"
        main_document = _content_addressed_document(
            main_path,
            "result bundle main artifact",
            document_class="result_main",
        )
        strategy = _verified_strategy_binding(
            main_document.get("strategy"),
            top_level_sha256=main_document.get("strategy_sha256"),
        )
        source = main_document.get("source")
        producer = _verified_producer_binding(
            main_document.get("producer_code"),
            expected_producer_binding=expected_producer_binding,
        )
        scope = _verified_result_scope(main_document.get("scope"))
        if (
            main_document.get("schema_version") != EXPECTED_RESULT_SCHEMA
            or main_document.get("strategy") != strategy
            or not isinstance(source, Mapping)
            or main_document.get("producer_code") != producer
            or main_document.get("scope") != scope
        ):
            raise ValueError("result bundle main binding is invalid")

        references = main_document.get("sidecars")
        if not isinstance(references, Mapping) or set(references) != set(
            RESULT_BUNDLE_SIDECAR_SCHEMAS
        ):
            raise ValueError("result bundle sidecar references are invalid")
        sidecar_dir = run_root / "sidecars"
        sidecar_entries = _directory_entries(
            sidecar_dir,
            "result bundle sidecar directory",
        )
        expected_names = {
            f"{digest}.json" for digest in sidecar_hashes.values()
        }
        if (
            len(sidecar_entries) != len(expected_names)
            or {path.name for path in sidecar_entries} != expected_names
        ):
            raise ValueError("result bundle physical sidecar set is not exact")

        sidecar_documents: dict[str, dict[str, Any]] = {}
        sidecar_paths: dict[str, Path] = {}
        for name, schema in RESULT_BUNDLE_SIDECAR_SCHEMAS.items():
            digest = sidecar_hashes[name]
            reference = references[name]
            if (
                not isinstance(reference, Mapping)
                or set(reference) != {"artifact_sha256", "relative_path"}
                or reference.get("artifact_sha256") != digest
                or reference.get("relative_path")
                != f"sidecars/{digest}.json"
            ):
                raise ValueError("result bundle sidecar reference is invalid")
            path = sidecar_dir / f"{digest}.json"
            document = _content_addressed_document(
                path,
                f"result bundle {name} sidecar",
                document_class=f"result_sidecar_{name}",
            )
            if (
                document.get("artifact_sha256") != digest
                or document.get("schema_version") != schema
                or document.get("strategy_sha256")
                != EXPECTED_STRATEGY_SHA256
                or document.get("source") != source
                or document.get("producer_code") != producer
            ):
                raise ValueError("result bundle sidecar binding is invalid")
            sidecar_documents[name] = document
            sidecar_paths[name] = path

        filter_receipt_sha256 = _verified_market_breadth_filter_receipt(
            sidecar_documents["selection"].get(
                "market_breadth_filter_receipt"
            )
        )
        feature_receipt_sha256 = _require_sha256(
            runtime.get("market_breadth_feature_binding_receipt_sha256"),
            "result bundle breadth feature receipt",
        )
        if (
            main_document.get("market_breadth_filter_receipt_sha256")
            != filter_receipt_sha256
            or filter_receipt_sha256 == feature_receipt_sha256
        ):
            raise ValueError("result bundle breadth receipt binding is invalid")

        return {
            "main_artifact_path": main_path,
            "main_artifact_sha256": main_sha256,
            "main_document": main_document,
            "sidecar_paths": sidecar_paths,
            "sidecar_documents": sidecar_documents,
            "sidecar_artifact_sha256": sidecar_hashes,
            "runtime_verification": runtime,
            "market_breadth_filter_receipt_sha256": (
                filter_receipt_sha256
            ),
            "market_breadth_feature_binding_receipt_sha256": (
                feature_receipt_sha256
            ),
        }
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("result bundle"):
            raise
        raise ValueError("result bundle is invalid") from exc


def _json_clone(value: object, label: str) -> Any:
    try:
        return json.loads(_canonical_bytes(value).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not canonical JSON") from exc


def _verified_signed_document(
    value: object,
    label: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    document = _json_clone(dict(value), label)
    artifact_sha256 = _require_sha256(
        document.pop("artifact_sha256", None),
        label,
    )
    if _sha256(document) != artifact_sha256:
        raise ValueError(f"{label} content addressing drifted")
    return document, artifact_sha256


def _verified_binding(
    value: object,
    *,
    schema_version: str,
    label: str,
    schema_in_root: bool = True,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    binding = _json_clone(dict(value), label)
    root_sha256 = _require_sha256(binding.pop("root_sha256", None), label)
    if binding.get("schema_version") != schema_version:
        raise ValueError(f"{label} schema drifted")
    root_body = dict(binding)
    if not schema_in_root:
        root_body.pop("schema_version")
    if _sha256(root_body) != root_sha256:
        raise ValueError(f"{label} root drifted")
    return {**binding, "root_sha256": root_sha256}, root_sha256


def _verified_risk_binding(
    value: object,
    label: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != PRODUCER_BINDING_FIELDS:
        raise ValueError(f"{label} fields drifted")
    binding, root_sha256 = _verified_binding(
        value,
        schema_version=EXPECTED_PRODUCER_SCHEMA,
        label=label,
    )
    for field in PRODUCER_BINDING_FIELDS - {
        "schema_version",
        "root_sha256",
    }:
        _require_sha256(binding.get(field), f"{label} {field}")
    if (
        binding.get("risk_on_breadth_strategy_sha256")
        != EXPECTED_STRATEGY_SHA256
    ):
        raise ValueError(f"{label} strategy drifted")
    return binding, root_sha256


def _xgboost_library_value(build_info: object, label: str) -> str:
    if not isinstance(build_info, Mapping):
        raise ValueError(f"{label} is invalid")
    library = build_info.get("libxgboost")
    if not isinstance(library, str) or not library:
        raise ValueError(f"{label} library is invalid")
    path = Path(library)
    if (
        library == NORMALIZED_XGBOOST_LIBRARY
        or not path.is_absolute()
        or path.name.lower() not in {"xgboost.dll", "libxgboost.dll"}
        or path.parent.name.lower() != "lib"
        or path.parent.parent.name.lower() != "xgboost"
    ):
        raise ValueError(f"{label} library path is invalid")
    return library


def _sha256_regular_file(path: Path, label: str) -> str:
    metadata_before = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata_before.st_mode):
        raise ValueError(f"{label} is not a regular file")

    def identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            int(metadata.st_mode),
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_dev),
            int(metadata.st_ino),
        )

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if identity(opened_before) != identity(metadata_before):
                raise ValueError(f"{label} changed before hashing")
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            opened_after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    metadata_after = _assert_no_reparse(path, label)
    if (
        identity(opened_after) != identity(opened_before)
        or identity(metadata_after) != identity(metadata_before)
    ):
        raise ValueError(f"{label} changed while hashing")
    return digest.hexdigest()


def _xgboost_distribution_attestation(library: Path) -> dict[str, str]:
    try:
        distribution = importlib_metadata.distribution("xgboost")
        items = sorted(
            distribution.files or (),
            key=lambda value: str(value),
        )
    except (importlib_metadata.PackageNotFoundError, OSError) as exc:
        raise ValueError("xgboost distribution is unavailable") from exc
    expected_library = library.absolute().resolve()
    entries: list[dict[str, Any]] = []
    library_sha256: str | None = None
    for item in items:
        path = Path(distribution.locate_file(item)).absolute().resolve()
        try:
            metadata = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        sha256 = _sha256_regular_file(
            path,
            f"xgboost distribution file {item}",
        )
        entries.append(
            {
                "path": str(item).replace("\\", "/"),
                "bytes": int(metadata.st_size),
                "sha256": sha256,
            }
        )
        if path == expected_library:
            library_sha256 = sha256
    if not entries or library_sha256 is None:
        raise ValueError("xgboost library is outside its distribution")
    return {
        "files_sha256": hashlib.sha256(
            json.dumps(
                entries,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "library_sha256": library_sha256,
    }


def _verified_formal_runtime_history(value: object) -> dict[str, Any]:
    required_fields = {
        "schema_version",
        "preflight_xgboost_distribution_files_sha256",
        "postflight_xgboost_distribution_files_sha256",
        "immutable_inputs_unchanged",
    }
    if not isinstance(value, Mapping) or set(value) != required_fields:
        raise ValueError("formal runtime history fields drifted")
    history = dict(value)
    preflight_sha256 = _require_sha256(
        history.get("preflight_xgboost_distribution_files_sha256"),
        "formal preflight xgboost distribution",
    )
    postflight_sha256 = _require_sha256(
        history.get("postflight_xgboost_distribution_files_sha256"),
        "formal postflight xgboost distribution",
    )
    if (
        history.get("schema_version") != FORMAL_RUNTIME_HISTORY_SCHEMA
        or history.get("immutable_inputs_unchanged") is not True
        or preflight_sha256 != postflight_sha256
    ):
        raise ValueError("formal runtime history is invalid")
    return history


def _normalized_shallow_binding(
    value: object,
    label: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    binding, _root_sha256 = _verified_binding(
        value,
        schema_version=(
            "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
        ),
        label=label,
    )
    build_info = binding.get("xgboost_build_info")
    _xgboost_library_value(build_info, f"{label} xgboost build info")
    normalized_build_info = _json_clone(
        dict(build_info),
        f"{label} xgboost build info",
    )
    normalized_build_info["libxgboost"] = NORMALIZED_XGBOOST_LIBRARY
    normalized_body = {
        key: _json_clone(item, f"{label} {key}")
        for key, item in binding.items()
        if key != "root_sha256"
    }
    normalized_body["xgboost_build_info"] = normalized_build_info
    normalized = {
        **normalized_body,
        "root_sha256": _sha256(normalized_body),
    }
    _verified_binding(
        normalized,
        schema_version=(
            "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
        ),
        label=f"{label} normalized",
    )
    return binding, binding["root_sha256"], normalized


def _normalized_risk_binding(
    value: object,
    *,
    expected_shallow_root_sha256: str,
    normalized_shallow_root_sha256: str,
    label: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    binding, root_sha256 = _verified_risk_binding(value, label)
    if (
        binding.get("base_shallow_gbdt_producer_root_sha256")
        != expected_shallow_root_sha256
    ):
        raise ValueError(f"{label} shallow dependency drifted")
    normalized_body = {
        key: _json_clone(item, f"{label} {key}")
        for key, item in binding.items()
        if key != "root_sha256"
    }
    normalized_body["base_shallow_gbdt_producer_root_sha256"] = (
        normalized_shallow_root_sha256
    )
    normalized = {
        **normalized_body,
        "root_sha256": _sha256(normalized_body),
    }
    _verified_risk_binding(normalized, f"{label} normalized")
    return binding, root_sha256, normalized


def _verified_execution_snapshot(
    value: object,
    *,
    expected: Mapping[str, Any],
    formal_runtime_history: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or not isinstance(expected, Mapping)
        or dict(value) != dict(expected)
    ):
        raise ValueError("execution snapshot binding drifted")
    snapshot = _json_clone(dict(value), "execution snapshot")
    snapshot_sha256 = _require_sha256(
        snapshot.pop("snapshot_sha256", None),
        "execution snapshot",
    )
    if (
        set(snapshot)
        != {"schema_version", "entrypoint", "xgboost_relocation"}
        or snapshot.get("schema_version") != EXECUTION_SNAPSHOT_SCHEMA
        or _sha256(snapshot) != snapshot_sha256
    ):
        raise ValueError("execution snapshot is invalid")

    entrypoint = snapshot.get("entrypoint")
    if not isinstance(entrypoint, Mapping) or set(entrypoint) != {
        "module",
        "dispatch",
        "argv",
        "output_dir",
        "driver_sha256",
        "direct_callable_used",
    }:
        raise ValueError("execution snapshot entrypoint drifted")
    output_dir = entrypoint.get("output_dir")
    if (
        not isinstance(output_dir, str)
        or not output_dir
        or entrypoint.get("module") != "app.jobs"
        or entrypoint.get("dispatch") != "runpy.run_module"
        or entrypoint.get("argv") != _jobs_argv(output_dir)
        or entrypoint.get("driver_sha256")
        != _sha256(ISOLATED_REPLAY_DRIVER)
        or entrypoint.get("direct_callable_used") is not False
        or REPLAY_PLAN.get("execution")
        != {
            "entrypoint": "app.jobs",
            "direct_callable_allowed": False,
        }
        or 'runpy.run_module("app.jobs"' not in ISOLATED_REPLAY_DRIVER
    ):
        raise ValueError("execution snapshot entrypoint is invalid")

    relocation = snapshot.get("xgboost_relocation")
    relocation_fields = {
        "formal_ridge_binding",
        "replay_ridge_binding",
        "formal_shallow_binding",
        "replay_shallow_binding",
        "formal_risk_binding",
        "replay_risk_binding",
        "formal_xgboost_library_sha256",
        "replay_xgboost_library_sha256",
        "formal_preflight_xgboost_distribution_files_sha256",
        "formal_postflight_xgboost_distribution_files_sha256",
    }
    if not isinstance(relocation, Mapping) or set(relocation) != relocation_fields:
        raise ValueError("execution snapshot relocation fields drifted")
    formal_ridge, formal_ridge_root = _verified_binding(
        relocation.get("formal_ridge_binding"),
        schema_version="audited-pit-ranked-liquidity-producer/v3",
        label="formal ridge binding",
        schema_in_root=False,
    )
    replay_ridge, replay_ridge_root = _verified_binding(
        relocation.get("replay_ridge_binding"),
        schema_version="audited-pit-ranked-liquidity-producer/v3",
        label="replay ridge binding",
        schema_in_root=False,
    )
    if formal_ridge != replay_ridge or formal_ridge_root != replay_ridge_root:
        raise ValueError("ridge relocation drifted")
    (
        formal_shallow,
        formal_shallow_root,
        normalized_formal_shallow,
    ) = _normalized_shallow_binding(
        relocation.get("formal_shallow_binding"),
        "formal shallow binding",
    )
    (
        replay_shallow,
        replay_shallow_root,
        normalized_replay_shallow,
    ) = _normalized_shallow_binding(
        relocation.get("replay_shallow_binding"),
        "replay shallow binding",
    )
    if (
        formal_shallow.get("base_ranked_liquidity_producer_root_sha256")
        != formal_ridge_root
        or replay_shallow.get("base_ranked_liquidity_producer_root_sha256")
        != replay_ridge_root
        or normalized_formal_shallow != normalized_replay_shallow
    ):
        raise ValueError("shallow relocation drifted")
    normalized_shallow_root = normalized_formal_shallow["root_sha256"]
    formal_risk, formal_risk_root, normalized_formal_risk = (
        _normalized_risk_binding(
            relocation.get("formal_risk_binding"),
            expected_shallow_root_sha256=formal_shallow_root,
            normalized_shallow_root_sha256=normalized_shallow_root,
            label="formal risk binding",
        )
    )
    replay_risk, _replay_risk_root, normalized_replay_risk = (
        _normalized_risk_binding(
            relocation.get("replay_risk_binding"),
            expected_shallow_root_sha256=replay_shallow_root,
            normalized_shallow_root_sha256=normalized_shallow_root,
            label="replay risk binding",
        )
    )
    if (
        formal_risk_root != EXPECTED_PRODUCER_ROOT_SHA256
        or normalized_formal_risk != normalized_replay_risk
    ):
        raise ValueError("risk relocation drifted")
    formal_build_info = formal_shallow["xgboost_build_info"]
    replay_build_info = replay_shallow["xgboost_build_info"]
    normalized_build_info = normalized_formal_shallow[
        "xgboost_build_info"
    ]
    if (
        {
            **formal_build_info,
            "libxgboost": NORMALIZED_XGBOOST_LIBRARY,
        }
        != normalized_build_info
        or {
            **replay_build_info,
            "libxgboost": NORMALIZED_XGBOOST_LIBRARY,
        }
        != normalized_build_info
    ):
        raise ValueError("xgboost build info relocation drifted")
    formal_library_sha256 = _require_sha256(
        relocation.get("formal_xgboost_library_sha256"),
        "formal xgboost library",
    )
    replay_library_sha256 = _require_sha256(
        relocation.get("replay_xgboost_library_sha256"),
        "replay xgboost library",
    )
    formal_library = Path(
        _xgboost_library_value(
            formal_build_info,
            "formal xgboost build info",
        )
    )
    replay_library = Path(
        _xgboost_library_value(
            replay_build_info,
            "replay xgboost build info",
        )
    )
    distribution_attestation = _xgboost_distribution_attestation(
        formal_library
    )
    historical_preflight_sha256 = _require_sha256(
        relocation.get(
            "formal_preflight_xgboost_distribution_files_sha256"
        ),
        "snapshot formal preflight xgboost distribution",
    )
    historical_postflight_sha256 = _require_sha256(
        relocation.get(
            "formal_postflight_xgboost_distribution_files_sha256"
        ),
        "snapshot formal postflight xgboost distribution",
    )
    if (
        historical_preflight_sha256
        != formal_runtime_history.get(
            "preflight_xgboost_distribution_files_sha256"
        )
        or historical_postflight_sha256
        != formal_runtime_history.get(
            "postflight_xgboost_distribution_files_sha256"
        )
        or historical_preflight_sha256 != historical_postflight_sha256
        or distribution_attestation.get("files_sha256")
        != historical_preflight_sha256
        or formal_library_sha256 != replay_library_sha256
        or distribution_attestation.get("library_sha256")
        != formal_library_sha256
        or _sha256_regular_file(
            formal_library,
            "formal xgboost library",
        )
        != formal_library_sha256
        or _sha256_regular_file(
            replay_library,
            "replay xgboost library",
        )
        != replay_library_sha256
    ):
        raise ValueError("xgboost library content drifted")
    return {
        "entrypoint": dict(entrypoint),
        "snapshot_sha256": snapshot_sha256,
        "xgboost_relocation": {
            "formal_risk_binding": formal_risk,
            "replay_risk_binding": replay_risk,
            "normalized_risk_binding": normalized_formal_risk,
            "formal_xgboost_build_info": formal_build_info,
            "replay_xgboost_build_info": replay_build_info,
            "normalized_xgboost_build_info": normalized_build_info,
            "xgboost_library_sha256": formal_library_sha256,
        },
    }


def _normalized_source(
    value: object,
    *,
    expected_risk_binding: Mapping[str, Any],
    normalized_risk_binding: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    source = _json_clone(dict(value), label)
    if source.get("producer_code") != dict(expected_risk_binding):
        raise ValueError(f"{label} producer drifted")
    source["producer_code"] = _json_clone(
        dict(normalized_risk_binding),
        f"{label} normalized producer",
    )
    return source


def _normalized_models_oof_sidecar(
    document: Mapping[str, Any],
    *,
    expected_xgboost_build_info: Mapping[str, Any],
    normalized_xgboost_build_info: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    normalized_document = _json_clone(dict(document), label)
    oof_receipt = normalized_document.get("oof_receipt")
    oof_verification = normalized_document.get("oof_replay_verification")
    if not isinstance(oof_receipt, dict) or not isinstance(
        oof_verification,
        dict,
    ):
        raise ValueError(f"{label} OOF envelope is invalid")
    oof_unsigned = dict(oof_receipt)
    oof_receipt_sha256 = _require_sha256(
        oof_unsigned.pop("receipt_sha256", None),
        f"{label} OOF receipt",
    )
    folds = oof_unsigned.get("folds")
    folds_sha256 = _require_sha256(
        oof_unsigned.get("folds_sha256"),
        f"{label} OOF folds",
    )
    if (
        not isinstance(folds, list)
        or not folds
        or oof_unsigned.get("fold_count") != len(folds)
        or folds_sha256 != _sha256(folds)
        or oof_receipt_sha256 != _sha256(oof_unsigned)
        or set(oof_verification)
        != {
            "verified",
            "receipt_sha256",
            "fold_count",
            "oof_candidate_count",
        }
        or oof_verification.get("verified") is not True
        or oof_verification.get("receipt_sha256") != oof_receipt_sha256
        or oof_verification.get("fold_count") != len(folds)
        or oof_verification.get("oof_candidate_count")
        != oof_unsigned.get("oof_candidate_count")
    ):
        raise ValueError(f"{label} OOF receipt binding drifted")
    normalized_folds: list[dict[str, Any]] = []
    for index, fold_value in enumerate(folds):
        if not isinstance(fold_value, Mapping):
            raise ValueError(f"{label} OOF fold {index} is invalid")
        fold_unsigned = _json_clone(
            dict(fold_value),
            f"{label} OOF fold {index}",
        )
        fold_receipt_sha256 = _require_sha256(
            fold_unsigned.pop("receipt_sha256", None),
            f"{label} OOF fold {index}",
        )
        if fold_receipt_sha256 != _sha256(fold_unsigned):
            raise ValueError(f"{label} OOF fold {index} receipt drifted")
        for receipt_name in ("fit_receipt", "predict_receipt"):
            receipt = fold_unsigned.get(receipt_name)
            if not isinstance(receipt, dict):
                raise ValueError(
                    f"{label} OOF fold {index} {receipt_name} is invalid"
                )
            runtime = receipt.get("runtime")
            if (
                not isinstance(runtime, dict)
                or runtime.get("xgboost_build_info")
                != dict(expected_xgboost_build_info)
            ):
                raise ValueError(
                    f"{label} OOF fold {index} runtime drifted"
                )
            runtime["xgboost_build_info"] = _json_clone(
                dict(normalized_xgboost_build_info),
                f"{label} normalized xgboost runtime",
            )
        fold_unsigned["receipt_sha256"] = _sha256(fold_unsigned)
        normalized_folds.append(fold_unsigned)
    normalized_oof = dict(oof_unsigned)
    normalized_oof["folds"] = normalized_folds
    normalized_oof["folds_sha256"] = _sha256(normalized_folds)
    normalized_oof["receipt_sha256"] = _sha256(normalized_oof)
    normalized_verification = dict(oof_verification)
    normalized_verification["receipt_sha256"] = normalized_oof[
        "receipt_sha256"
    ]
    normalized_document["oof_receipt"] = normalized_oof
    normalized_document["oof_replay_verification"] = normalized_verification
    return normalized_document


def _relocation_normalized_bundle(
    value: object,
    *,
    expected_risk_binding: Mapping[str, Any],
    normalized_risk_binding: Mapping[str, Any],
    expected_xgboost_build_info: Mapping[str, Any],
    normalized_xgboost_build_info: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    main_unsigned, main_artifact_sha256 = _verified_signed_document(
        value.get("main_document"),
        f"{label} main artifact",
    )
    if (
        value.get("main_artifact_sha256") != main_artifact_sha256
        or main_unsigned.get("schema_version") != EXPECTED_RESULT_SCHEMA
        or main_unsigned.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or main_unsigned.get("producer_code") != dict(expected_risk_binding)
    ):
        raise ValueError(f"{label} main binding drifted")
    _verified_strategy_binding(
        main_unsigned.get("strategy"),
        top_level_sha256=main_unsigned.get("strategy_sha256"),
    )
    _verified_result_scope(main_unsigned.get("scope"))
    source = main_unsigned.get("source")
    normalized_source = _normalized_source(
        source,
        expected_risk_binding=expected_risk_binding,
        normalized_risk_binding=normalized_risk_binding,
        label=f"{label} source",
    )

    sidecar_values = value.get("sidecar_documents")
    sidecar_hash_values = value.get("sidecar_artifact_sha256")
    references = main_unsigned.get("sidecars")
    if (
        not isinstance(sidecar_values, Mapping)
        or set(sidecar_values) != set(RESULT_BUNDLE_SIDECAR_SCHEMAS)
        or not isinstance(sidecar_hash_values, Mapping)
        or set(sidecar_hash_values) != set(RESULT_BUNDLE_SIDECAR_SCHEMAS)
        or not isinstance(references, Mapping)
        or set(references) != set(RESULT_BUNDLE_SIDECAR_SCHEMAS)
    ):
        raise ValueError(f"{label} sidecar set drifted")
    normalized_sidecars: dict[str, dict[str, Any]] = {}
    semantic_sidecar_hashes: dict[str, str] = {}
    for name in sorted(RESULT_BUNDLE_SIDECAR_SCHEMAS):
        sidecar_unsigned, sidecar_artifact_sha256 = (
            _verified_signed_document(
                sidecar_values[name],
                f"{label} {name} sidecar",
            )
        )
        reference = references[name]
        if (
            sidecar_hash_values.get(name) != sidecar_artifact_sha256
            or not isinstance(reference, Mapping)
            or set(reference) != {"artifact_sha256", "relative_path"}
            or reference.get("artifact_sha256") != sidecar_artifact_sha256
            or reference.get("relative_path")
            != f"sidecars/{sidecar_artifact_sha256}.json"
            or sidecar_unsigned.get("schema_version")
            != RESULT_BUNDLE_SIDECAR_SCHEMAS[name]
            or sidecar_unsigned.get("strategy_sha256")
            != EXPECTED_STRATEGY_SHA256
            or sidecar_unsigned.get("producer_code")
            != dict(expected_risk_binding)
            or sidecar_unsigned.get("source") != source
        ):
            raise ValueError(f"{label} {name} sidecar binding drifted")
        normalized_sidecar = (
            _normalized_models_oof_sidecar(
                sidecar_unsigned,
                expected_xgboost_build_info=expected_xgboost_build_info,
                normalized_xgboost_build_info=(
                    normalized_xgboost_build_info
                ),
                label=f"{label} models sidecar",
            )
            if name == "models"
            else _json_clone(
                sidecar_unsigned,
                f"{label} {name} sidecar",
            )
        )
        normalized_sidecar["producer_code"] = _json_clone(
            dict(normalized_risk_binding),
            f"{label} normalized producer",
        )
        normalized_sidecar["source"] = normalized_source
        normalized_sidecars[name] = normalized_sidecar
        semantic_sidecar_hashes[name] = _sha256(normalized_sidecar)

    filter_receipt_sha256 = _verified_market_breadth_filter_receipt(
        sidecar_values["selection"].get(
            "market_breadth_filter_receipt"
        )
    )
    if (
        main_unsigned.get("market_breadth_filter_receipt_sha256")
        != filter_receipt_sha256
        or value.get("market_breadth_filter_receipt_sha256")
        != filter_receipt_sha256
    ):
        raise ValueError(f"{label} breadth filter receipt drifted")

    normalized_main = _json_clone(main_unsigned, f"{label} main artifact")
    normalized_main["producer_code"] = _json_clone(
        dict(normalized_risk_binding),
        f"{label} normalized producer",
    )
    normalized_main["source"] = normalized_source
    normalized_main["sidecars"] = {
        name: {"semantic_sha256": semantic_sidecar_hashes[name]}
        for name in sorted(semantic_sidecar_hashes)
    }
    main_semantic_sha256 = _sha256(normalized_main)

    runtime_unsigned, _runtime_artifact_sha256 = _verified_signed_document(
        value.get("runtime_verification"),
        f"{label} runtime verification",
    )
    runtime_receipt_body = dict(runtime_unsigned)
    runtime_receipt_sha256 = _require_sha256(
        runtime_receipt_body.pop("receipt_sha256", None),
        f"{label} runtime verification receipt",
    )
    required_runtime_fields = {
        "schema_version",
        "strategy_sha256",
        "producer_root_sha256",
        "main_artifact_sha256",
        "sidecar_artifact_sha256",
        "checks",
        "market_breadth_feature_binding_receipt_sha256",
        "verified",
    }
    checks = runtime_receipt_body.get("checks")
    feature_receipt_sha256 = _require_sha256(
        runtime_receipt_body.get(
            "market_breadth_feature_binding_receipt_sha256"
        ),
        f"{label} market breadth feature binding receipt",
    )
    if (
        set(runtime_receipt_body) != required_runtime_fields
        or runtime_receipt_sha256 != _sha256(runtime_receipt_body)
        or runtime_receipt_body.get("schema_version")
        != EXPECTED_VERIFICATION_SCHEMA
        or runtime_receipt_body.get("strategy_sha256")
        != EXPECTED_STRATEGY_SHA256
        or runtime_receipt_body.get("producer_root_sha256")
        != expected_risk_binding.get("root_sha256")
        or runtime_receipt_body.get("main_artifact_sha256")
        != main_artifact_sha256
        or runtime_receipt_body.get("sidecar_artifact_sha256")
        != dict(sidecar_hash_values)
        or not isinstance(checks, Mapping)
        or set(checks) != set(REQUIRED_VERIFICATION_CHECKS)
        or any(item is not True for item in checks.values())
        or runtime_receipt_body.get("verified") is not True
        or value.get(
            "market_breadth_feature_binding_receipt_sha256"
        )
        != feature_receipt_sha256
        or feature_receipt_sha256 == filter_receipt_sha256
    ):
        raise ValueError(f"{label} runtime verification drifted")
    normalized_runtime = _json_clone(
        runtime_receipt_body,
        f"{label} runtime verification",
    )
    normalized_runtime["producer_root_sha256"] = normalized_risk_binding[
        "root_sha256"
    ]
    normalized_runtime["main_artifact_sha256"] = main_semantic_sha256
    normalized_runtime["sidecar_artifact_sha256"] = semantic_sidecar_hashes
    runtime_semantic_sha256 = _sha256(normalized_runtime)
    normalized_bundle = {
        "main": normalized_main,
        "sidecars": normalized_sidecars,
        "runtime_verification": normalized_runtime,
    }
    return {
        "normalized_bundle": normalized_bundle,
        "main_semantic_sha256": main_semantic_sha256,
        "sidecar_semantic_sha256": semantic_sidecar_hashes,
        "runtime_verification_semantic_sha256": runtime_semantic_sha256,
        "market_breadth_feature_binding_receipt_sha256": (
            feature_receipt_sha256
        ),
    }


def _validate_replay(
    inputs: Mapping[str, Any],
    replay: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    _assert_frozen_replay_plan()
    try:
        if (
            not isinstance(inputs, Mapping)
            or not isinstance(replay, Mapping)
            or set(replay)
            != {"schema_version", "execution_snapshot", "result_bundle"}
            or replay.get("schema_version") != REPLAY_RESULT_SCHEMA
        ):
            raise ValueError
        formal_runtime_history = _verified_formal_runtime_history(
            inputs.get("formal_runtime_history")
        )
        snapshot_contract = _verified_execution_snapshot(
            replay.get("execution_snapshot"),
            expected=execution_snapshot,
            formal_runtime_history=formal_runtime_history,
        )
        formal_bundle = inputs.get("result_bundle")
        replay_bundle = replay.get("result_bundle")
        if not isinstance(formal_bundle, Mapping) or not isinstance(
            replay_bundle,
            Mapping,
        ):
            raise ValueError

        relocation = snapshot_contract["xgboost_relocation"]
        formal_semantics = _relocation_normalized_bundle(
            formal_bundle,
            expected_risk_binding=relocation["formal_risk_binding"],
            normalized_risk_binding=relocation["normalized_risk_binding"],
            expected_xgboost_build_info=relocation[
                "formal_xgboost_build_info"
            ],
            normalized_xgboost_build_info=relocation[
                "normalized_xgboost_build_info"
            ],
            label="formal result bundle",
        )
        replay_semantics = _relocation_normalized_bundle(
            replay_bundle,
            expected_risk_binding=relocation["replay_risk_binding"],
            normalized_risk_binding=relocation["normalized_risk_binding"],
            expected_xgboost_build_info=relocation[
                "replay_xgboost_build_info"
            ],
            normalized_xgboost_build_info=relocation[
                "normalized_xgboost_build_info"
            ],
            label="replayed result bundle",
        )
        formal_feature_receipt = formal_semantics[
            "market_breadth_feature_binding_receipt_sha256"
        ]
        replay_feature_receipt = replay_semantics[
            "market_breadth_feature_binding_receipt_sha256"
        ]
        if (
            formal_feature_receipt != replay_feature_receipt
            or formal_semantics["normalized_bundle"]
            != replay_semantics["normalized_bundle"]
        ):
            raise ValueError

        formal_sha256 = _sha256(formal_semantics["normalized_bundle"])
        replay_sha256 = _sha256(replay_semantics["normalized_bundle"])
        if formal_sha256 != replay_sha256:
            raise ValueError
        body = {
            "schema_version": RELOCATION_EQUIVALENCE_SCHEMA,
            "verified": True,
            "execution_snapshot_sha256": snapshot_contract[
                "snapshot_sha256"
            ],
            "entrypoint_envelope_sha256": _sha256(
                snapshot_contract["entrypoint"]
            ),
            "xgboost_library_sha256": relocation[
                "xgboost_library_sha256"
            ],
            "formal_semantic_sha256": formal_sha256,
            "replay_semantic_sha256": replay_sha256,
            "main_semantic_sha256": formal_semantics[
                "main_semantic_sha256"
            ],
            "sidecar_semantic_sha256": formal_semantics[
                "sidecar_semantic_sha256"
            ],
            "runtime_verification_semantic_sha256": formal_semantics[
                "runtime_verification_semantic_sha256"
            ],
            "market_breadth_feature_binding_receipt_sha256": (
                formal_feature_receipt
            ),
        }
        return {**body, "receipt_sha256": _sha256(body)}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("independent replay is invalid") from exc


def _receipt_body(*, verification_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "verification_sha256": _require_sha256(
            verification_sha256,
            "independent verification receipt",
        ),
        "verified": True,
        "point_in_time": True,
        "development_only": True,
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "production_authority": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "terminal_status_required": True,
        "receipt_alone_authoritative": False,
    }


def _status_payload(
    *,
    verified: bool,
    receipt_sha256: str | None,
    error_type: str | None,
) -> dict[str, Any]:
    if not isinstance(verified, bool):
        raise ValueError("independent verification status is invalid")
    if verified:
        receipt = _require_sha256(
            receipt_sha256,
            "independent verification status receipt",
        )
        if error_type is not None:
            raise ValueError("independent verification status is invalid")
        status = "completed"
    else:
        if receipt_sha256 is not None or not isinstance(error_type, str) or not error_type:
            raise ValueError("independent verification status is invalid")
        receipt = None
        status = "failed"
    return {
        "schema_version": STATUS_SCHEMA,
        "status": status,
        "stage": status,
        "verified": verified,
        "receipt_sha256": receipt,
        "error_type": error_type,
        "point_in_time": True,
        "development_only": True,
        "development_statistical_interpretation_allowed": verified,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "production_authority": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }


class IndependentVerificationError(RuntimeError):
    pass


class IndependentReplayProcessError(IndependentVerificationError):
    def __init__(self, exit_code: int) -> None:
        super().__init__("independent replay returned nonzero")
        self.exit_code = exit_code


class ClaimOwnershipError(IndependentVerificationError):
    pass


CONTROL_FILE_LOCK_LENGTH = 1024 * 1024 * 1024


def _lock_control_file(handle: Any, label: str) -> bool:
    if msvcrt is None:
        return False
    try:
        handle.seek(0)
        msvcrt.locking(
            handle.fileno(),
            msvcrt.LK_NBLCK,
            CONTROL_FILE_LOCK_LENGTH,
        )
        handle.seek(0)
    except OSError as exc:
        raise IndependentVerificationError(f"{label} lock failed") from exc
    return True


def _open_control_file(
    path: Path,
    *,
    create: bool,
    writable: bool,
) -> Any:
    if os.name != "nt":
        if create:
            return path.open("xb+")
        return path.open("r+b" if writable else "rb")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    desired_access = 0x80000000 | (0x40000000 if writable else 0)
    native_handle = create_file(
        str(path),
        desired_access,
        0x00000001,
        None,
        1 if create else 3,
        0x00000080,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if native_handle == invalid_handle:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code), str(path))
    flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
    try:
        descriptor = msvcrt.open_osfhandle(native_handle, flags)
    except BaseException:
        kernel32.CloseHandle(native_handle)
        raise
    return os.fdopen(descriptor, "r+b" if writable else "rb")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once(
    path: Path,
    raw: bytes,
    label: str,
    *,
    hold_ownership: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(raw, bytes):
        raise TypeError(f"{label} bytes are invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_before = _assert_no_reparse(path.parent, f"{label} parent")
    if not stat.S_ISDIR(parent_before.st_mode):
        raise ValueError(f"{label} parent is invalid")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{os.urandom(12).hex()}.tmp"
    )
    linked_object_identity: tuple[int, int, int] | None = None
    handle = None
    ownership: dict[str, Any] | None = None
    try:
        handle = _open_control_file(
            temporary,
            create=True,
            writable=True,
        )
        written = handle.write(raw)
        if written != len(raw):
            raise OSError(f"{label} short write")
        handle.flush()
        os.fsync(handle.fileno())
        locked = _lock_control_file(handle, label)
        os.link(temporary, path)
        opened_after_link = os.fstat(handle.fileno())
        linked_object_identity = _stable_object_identity(opened_after_link)
        target_before = _assert_no_reparse(path, label)
        if (
            not stat.S_ISREG(opened_after_link.st_mode)
            or _stable_object_identity(target_before) != linked_object_identity
            or _owned_file_identity(target_before)
            != _owned_file_identity(opened_after_link)
        ):
            raise ValueError(f"{label} publication identity changed")
        handle.seek(0)
        observed = handle.read()
        opened_after_read = os.fstat(handle.fileno())
        target_after = _assert_no_reparse(path, label)
        parent_after = _assert_no_reparse(path.parent, f"{label} parent")
        if _stable_object_identity(parent_after) != _stable_object_identity(
            parent_before
        ):
            raise ValueError(f"{label} parent changed during publication")
        if (
            observed != raw
            or hashlib.sha256(observed).digest()
            != hashlib.sha256(raw).digest()
            or _owned_file_identity(opened_after_read)
            != _owned_file_identity(opened_after_link)
            or _owned_file_identity(target_after)
            != _owned_file_identity(opened_after_link)
        ):
            raise ValueError(f"{label} publication changed or differs")
        if hold_ownership:
            ownership = {
                "path": path,
                "handle": handle,
                "raw": raw,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "identity": _owned_file_identity(opened_after_read),
                "object_identity": linked_object_identity,
                "parent_identity": _stable_object_identity(parent_before),
                "temporary_path": temporary,
                "locked": locked,
                "label": label,
            }
            return ownership
        return None
    except BaseException:
        opened_object_identity = None
        if handle is not None:
            try:
                opened_object_identity = _stable_object_identity(
                    os.fstat(handle.fileno())
                )
            except OSError:
                pass
            handle.close()
            handle = None
        owned_object_identity = (
            linked_object_identity
            if linked_object_identity is not None
            else opened_object_identity
        )
        if owned_object_identity is not None:
            try:
                current = _assert_no_reparse(path, label)
                if _stable_object_identity(current) == owned_object_identity:
                    path.unlink()
            except BaseException:
                pass
        raise
    finally:
        if ownership is None:
            if handle is not None:
                handle.close()
            temporary.unlink(missing_ok=True)


def _verify_publication_ownership(ownership: Mapping[str, Any]) -> None:
    try:
        path = ownership["path"]
        handle = ownership["handle"]
        raw = ownership["raw"]
        identity = ownership["identity"]
        label = str(ownership["label"])
        if not isinstance(path, Path) or not isinstance(raw, bytes):
            raise TypeError("publication ownership is invalid")
        metadata_before = _assert_no_reparse(path, label)
        opened_before = os.fstat(handle.fileno())
        handle.seek(0)
        observed = handle.read()
        opened_after = os.fstat(handle.fileno())
        metadata_after = _assert_no_reparse(path, label)
        parent_after = _assert_no_reparse(path.parent, f"{label} parent")
        if (
            _owned_file_identity(metadata_before) != identity
            or _owned_file_identity(opened_before) != identity
            or _owned_file_identity(opened_after) != identity
            or _owned_file_identity(metadata_after) != identity
            or _stable_object_identity(parent_after)
            != ownership["parent_identity"]
            or observed != raw
            or hashlib.sha256(observed).hexdigest() != ownership["sha256"]
        ):
            raise IndependentVerificationError(f"{label} ownership changed")
    except IndependentVerificationError:
        raise
    except BaseException as exc:
        raise IndependentVerificationError(
            "published control-file ownership is unavailable"
        ) from exc


def _close_publication_ownership(ownership: Mapping[str, Any] | None) -> None:
    if ownership is None:
        return
    handle = ownership.get("handle")
    if handle is not None and not handle.closed:
        handle.close()
    temporary = ownership.get("temporary_path")
    if isinstance(temporary, Path):
        temporary.unlink(missing_ok=True)


def _open_claim_ownership(path: Path, expected_raw: bytes) -> dict[str, Any]:
    if not isinstance(expected_raw, bytes):
        raise TypeError("claim ownership bytes are invalid")
    label = "independent verification retry_1 claim ownership"
    metadata_before = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata_before.st_mode):
        raise ClaimOwnershipError("claim ownership is invalid")
    handle = None
    try:
        handle = _open_control_file(
            path,
            create=False,
            writable=False,
        )
        locked = _lock_control_file(handle, label)
        opened_before = os.fstat(handle.fileno())
        if _owned_file_identity(opened_before) != _owned_file_identity(
            metadata_before
        ):
            raise ClaimOwnershipError("claim ownership changed before open")
        raw = handle.read()
        opened_after = os.fstat(handle.fileno())
        metadata_after = _assert_no_reparse(path, label)
        identity = _owned_file_identity(metadata_before)
        if (
            raw != expected_raw
            or _owned_file_identity(opened_after) != identity
            or _owned_file_identity(metadata_after) != identity
        ):
            raise ClaimOwnershipError("claim ownership validation failed")
        return {
            "path": path,
            "handle": handle,
            "raw": expected_raw,
            "sha256": hashlib.sha256(expected_raw).hexdigest(),
            "identity": identity,
            "object_identity": _stable_object_identity(opened_before),
            "parent_identity": _stable_object_identity(
                _assert_no_reparse(path.parent, f"{label} parent")
            ),
            "temporary_path": None,
            "locked": locked,
            "label": label,
        }
    except BaseException:
        if handle is not None:
            handle.close()
        raise


def _verify_claim_ownership(ownership: Mapping[str, Any]) -> None:
    label = "independent verification retry_1 claim ownership"
    try:
        path = ownership["path"]
        handle = ownership["handle"]
        expected_raw = ownership["raw"]
        expected_sha256 = ownership["sha256"]
        expected_identity = ownership["identity"]
        if not isinstance(path, Path) or not isinstance(expected_raw, bytes):
            raise TypeError("claim ownership is invalid")
        metadata_before = _assert_no_reparse(path, label)
        opened_before = os.fstat(handle.fileno())
        handle.seek(0)
        raw = handle.read()
        opened_after = os.fstat(handle.fileno())
        metadata_after = _assert_no_reparse(path, label)
        if (
            _owned_file_identity(metadata_before) != expected_identity
            or _owned_file_identity(opened_before) != expected_identity
            or _owned_file_identity(opened_after) != expected_identity
            or _owned_file_identity(metadata_after) != expected_identity
            or raw != expected_raw
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ClaimOwnershipError("claim ownership changed")
    except ClaimOwnershipError:
        raise
    except BaseException as exc:
        raise ClaimOwnershipError("claim ownership is unavailable") from exc


def _reserve_status_slot(path: Path) -> dict[str, Any]:
    label = "independent verification retry_1 status slot"
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_before = _assert_no_reparse(path.parent, f"{label} parent")
    if not stat.S_ISDIR(parent_before.st_mode):
        raise IndependentVerificationError(f"{label} parent is invalid")
    handle = None
    try:
        handle = _open_control_file(
            path,
            create=True,
            writable=True,
        )
        locked = _lock_control_file(handle, label)
        handle.flush()
        os.fsync(handle.fileno())
        opened = os.fstat(handle.fileno())
        metadata = _assert_no_reparse(path, label)
        parent_after = _assert_no_reparse(path.parent, f"{label} parent")
        identity = _owned_file_identity(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != 0
            or _owned_file_identity(metadata) != identity
            or _stable_object_identity(parent_after)
            != _stable_object_identity(parent_before)
        ):
            raise IndependentVerificationError(f"{label} reservation failed")
        return {
            "path": path,
            "handle": handle,
            "identity": identity,
            "object_identity": _stable_object_identity(opened),
            "parent_identity": _stable_object_identity(parent_before),
            "finalized": False,
            "locked": locked,
            "label": label,
        }
    except BaseException:
        if handle is not None:
            handle.close()
        raise


def _verify_empty_status_slot(ownership: Mapping[str, Any]) -> None:
    label = "independent verification retry_1 status slot"
    try:
        path = ownership["path"]
        handle = ownership["handle"]
        if ownership.get("finalized") is not False or not isinstance(path, Path):
            raise TypeError("status slot ownership is invalid")
        parent = _assert_no_reparse(path.parent, f"{label} parent")
        metadata = _assert_no_reparse(path, label)
        opened = os.fstat(handle.fileno())
        if (
            _stable_object_identity(parent) != ownership["parent_identity"]
            or _owned_file_identity(metadata) != ownership["identity"]
            or _owned_file_identity(opened) != ownership["identity"]
            or opened.st_size != 0
        ):
            raise IndependentVerificationError(f"{label} ownership changed")
    except IndependentVerificationError:
        raise
    except BaseException as exc:
        raise IndependentVerificationError(f"{label} is unavailable") from exc


def _finalize_status_slot(
    ownership: dict[str, Any],
    raw: bytes,
    *,
    claim_ownership: Mapping[str, Any],
    publication_ownerships: tuple[Mapping[str, Any], ...] = (),
    replay_stream_ownerships: tuple[Mapping[str, Any], ...] = (),
) -> None:
    if not isinstance(raw, bytes):
        raise TypeError("status slot bytes are invalid")
    _verify_claim_ownership(claim_ownership)
    for publication_ownership in publication_ownerships:
        _verify_publication_ownership(publication_ownership)
    for replay_stream_ownership in replay_stream_ownerships:
        _verify_retry_replay_stream_ownership(replay_stream_ownership)
    _verify_empty_status_slot(ownership)
    handle = ownership["handle"]
    path = ownership["path"]
    try:
        handle.seek(0)
        written = handle.write(raw)
        if written != len(raw):
            raise OSError("independent verification retry_1 status short write")
        handle.flush()
        os.fsync(handle.fileno())
        opened = os.fstat(handle.fileno())
        metadata = _assert_no_reparse(
            path,
            "independent verification retry_1 status slot",
        )
        handle.seek(0)
        observed = handle.read()
        if (
            _stable_object_identity(opened) != ownership["object_identity"]
            or _stable_object_identity(metadata) != ownership["object_identity"]
            or observed != raw
        ):
            raise IndependentVerificationError(
                "independent verification retry_1 status slot changed"
            )
        _verify_claim_ownership(claim_ownership)
        for publication_ownership in publication_ownerships:
            _verify_publication_ownership(publication_ownership)
        for replay_stream_ownership in replay_stream_ownerships:
            _verify_retry_replay_stream_ownership(replay_stream_ownership)
        handle.seek(0)
        final_raw = handle.read()
        final_opened = os.fstat(handle.fileno())
        final_metadata = _assert_no_reparse(
            path,
            "independent verification retry_1 status slot",
        )
        if (
            final_raw != raw
            or _stable_object_identity(final_opened)
            != ownership["object_identity"]
            or _stable_object_identity(final_metadata)
            != ownership["object_identity"]
        ):
            raise IndependentVerificationError(
                "independent verification retry_1 status slot changed"
            )
    except BaseException:
        try:
            current = _assert_no_reparse(
                path,
                "independent verification retry_1 status slot",
            )
            opened = os.fstat(handle.fileno())
            if (
                _stable_object_identity(current) == ownership["object_identity"]
                and _stable_object_identity(opened)
                == ownership["object_identity"]
            ):
                handle.seek(0)
                handle.truncate(0)
                handle.flush()
                os.fsync(handle.fileno())
                restored_opened = os.fstat(handle.fileno())
                restored_current = _assert_no_reparse(
                    path,
                    "independent verification retry_1 status slot",
                )
                restored_parent = _assert_no_reparse(
                    path.parent,
                    "independent verification retry_1 status slot parent",
                )
                if (
                    restored_opened.st_size == 0
                    and _stable_object_identity(restored_opened)
                    == ownership["object_identity"]
                    and _stable_object_identity(restored_current)
                    == ownership["object_identity"]
                    and _stable_object_identity(restored_parent)
                    == ownership["parent_identity"]
                ):
                    try:
                        _verify_claim_ownership(claim_ownership)
                    except BaseException:
                        pass
                    else:
                        ownership["identity"] = _owned_file_identity(
                            restored_opened
                        )
        except BaseException:
            pass
        raise
    ownership["finalized"] = True


def _json_raw(value: Mapping[str, Any]) -> bytes:
    return _canonical_bytes(dict(value)) + b"\n"


def _read_launcher_json(
    path: Path,
    label: str,
    *,
    fields: frozenset[str] | None = None,
) -> tuple[dict[str, Any], bytes]:
    try:
        value, raw = _read_json_bytes(
            path,
            label,
            document_class="formal_control",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if (
        not isinstance(value, dict)
        or raw != _json_raw(value)
        or (fields is not None and set(value) != set(fields))
    ):
        raise ValueError(f"{label} is invalid")
    return value, raw


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("git inspection failed")
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise IndependentVerificationError("git output is invalid") from exc


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("git inspection failed")
    return completed.stdout


def _git_run(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("git worktree operation failed")


def _assert_snapshot_identity(
    code_root: Path,
    *,
    source_commit: str,
    source_tree: str,
) -> None:
    if (
        _git_output(code_root, "rev-parse", "HEAD").lower() != source_commit
        or _git_output(code_root, "rev-parse", "HEAD^{tree}").lower()
        != source_tree
        or _git_output(
            code_root,
            "status",
            "--porcelain",
            "--untracked-files=all",
        )
    ):
        raise IndependentVerificationError("detached source snapshot drifted")


@contextmanager
def _detached_source_snapshot(
    source_root: Path,
    source_authority: Mapping[str, Any],
    scratch_root: Path,
) -> Iterator[Path]:
    source_commit = str(source_authority.get("source_commit") or "").lower()
    source_tree = str(source_authority.get("source_tree") or "").lower()
    if not HEX_GIT_SHA1.fullmatch(source_commit) or not HEX_GIT_SHA1.fullmatch(
        source_tree
    ):
        raise IndependentVerificationError("source authority commit is invalid")
    code_root = scratch_root / "code"
    scratch_root.mkdir(parents=True, exist_ok=False)
    _git_run(
        source_root,
        "worktree",
        "add",
        "--detach",
        str(code_root),
        source_commit,
    )
    try:
        _assert_snapshot_identity(
            code_root,
            source_commit=source_commit,
            source_tree=source_tree,
        )
        yield code_root
        _assert_snapshot_identity(
            code_root,
            source_commit=source_commit,
            source_tree=source_tree,
        )
    finally:
        try:
            _git_run(source_root, "worktree", "remove", "--force", str(code_root))
        finally:
            if code_root.exists():
                shutil.rmtree(code_root)


def _assert_sqlite_quiescent(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            raise ValueError("SQLite frozen input is not quiescent")


def _manifest_path(path: Path, label: str) -> dict[str, Any]:
    metadata = _assert_no_reparse(path, label)
    if stat.S_ISREG(metadata.st_mode):
        body = {
            "kind": "file",
            "bytes": metadata.st_size,
            "sha256": _file_sha256(path),
        }
        return {**body, "manifest_sha256": _sha256(body)}
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} is invalid")
    entries: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        candidate_metadata = _assert_no_reparse(candidate, label)
        if stat.S_ISDIR(candidate_metadata.st_mode):
            continue
        if not stat.S_ISREG(candidate_metadata.st_mode):
            raise ValueError(f"{label} contains an invalid entry")
        entries.append(
            {
                "path": candidate.relative_to(path).as_posix(),
                "bytes": candidate_metadata.st_size,
                "sha256": _file_sha256(candidate),
            }
        )
    body = {"kind": "directory_tree", "entries": entries}
    return {**body, "manifest_sha256": _sha256(body)}


def _regular_file_manifest(path: Path, label: str) -> dict[str, Any]:
    metadata_before = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata_before.st_mode):
        raise ValueError(f"{label} is not a regular file")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if _metadata_identity(opened_before) != _metadata_identity(
                metadata_before
            ):
                raise ValueError(f"{label} changed before open")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
            opened_after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    metadata_after = _assert_no_reparse(path, label)
    if (
        size != metadata_before.st_size
        or _metadata_identity(opened_after) != _metadata_identity(opened_before)
        or _metadata_identity(metadata_after) != _metadata_identity(metadata_before)
    ):
        raise ValueError(f"{label} changed while hashing")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _complete_directory_manifest(path: Path, label: str) -> dict[str, Any]:
    root_metadata = _assert_no_reparse(path, label)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError(f"{label} is not a directory")
    entries: list[dict[str, Any]] = []
    directory_snapshots: list[
        tuple[Path, tuple[int, int, int, int, int], list[tuple[str, str, tuple[int, int, int, int, int]]]]
    ] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        directory_before = _assert_no_reparse(directory, label)
        if not stat.S_ISDIR(directory_before.st_mode):
            raise ValueError(f"{label} contains an invalid entry")
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda candidate: candidate.name.casefold(),
            )
        except OSError as exc:
            raise ValueError(f"{label} is unreadable") from exc
        directory_after_enumeration = _assert_no_reparse(directory, label)
        if _metadata_identity(directory_after_enumeration) != _metadata_identity(
            directory_before
        ):
            raise ValueError(f"{label} changed while enumerating")
        child_snapshot: list[
            tuple[str, str, tuple[int, int, int, int, int]]
        ] = []
        child_directories: list[Path] = []
        for candidate in children:
            metadata_before = _assert_no_reparse(candidate, label)
            relative = candidate.relative_to(path).as_posix()
            if stat.S_ISDIR(metadata_before.st_mode):
                child_snapshot.append(
                    ("directory", candidate.name, _metadata_identity(metadata_before))
                )
                entries.append({"kind": "directory", "path": relative})
                child_directories.append(candidate)
                continue
            if not stat.S_ISREG(metadata_before.st_mode):
                raise ValueError(f"{label} contains an invalid entry")
            child_snapshot.append(
                ("file", candidate.name, _metadata_identity(metadata_before))
            )
            file_manifest = _regular_file_manifest(candidate, label)
            entries.append(
                {
                    "kind": "file",
                    "path": relative,
                    **file_manifest,
                }
            )
        directory_snapshots.append(
            (directory, _metadata_identity(directory_before), child_snapshot)
        )
        pending.extend(reversed(child_directories))
    for directory, directory_identity, expected_children in directory_snapshots:
        directory_after = _assert_no_reparse(directory, label)
        if _metadata_identity(directory_after) != directory_identity:
            raise ValueError(f"{label} changed after recursive enumeration")
        try:
            current_paths = sorted(
                directory.iterdir(),
                key=lambda candidate: candidate.name.casefold(),
            )
        except OSError as exc:
            raise ValueError(f"{label} is unreadable") from exc
        current_children: list[
            tuple[str, str, tuple[int, int, int, int, int]]
        ] = []
        for candidate in current_paths:
            candidate_metadata = _assert_no_reparse(candidate, label)
            if stat.S_ISDIR(candidate_metadata.st_mode):
                kind = "directory"
            elif stat.S_ISREG(candidate_metadata.st_mode):
                kind = "file"
            else:
                raise ValueError(f"{label} contains an invalid entry")
            current_children.append(
                (kind, candidate.name, _metadata_identity(candidate_metadata))
            )
        if current_children != expected_children:
            raise ValueError(f"{label} exact entries changed after recursion")
        directory_after_candidates = _assert_no_reparse(directory, label)
        if _metadata_identity(directory_after_candidates) != directory_identity:
            raise ValueError(f"{label} changed during final candidate scan")
        try:
            final_paths = sorted(
                directory.iterdir(),
                key=lambda candidate: candidate.name.casefold(),
            )
        except OSError as exc:
            raise ValueError(f"{label} is unreadable") from exc
        directory_after_final_enumeration = _assert_no_reparse(directory, label)
        if (
            _metadata_identity(directory_after_final_enumeration)
            != directory_identity
        ):
            raise ValueError(f"{label} changed during final enumeration")
        final_children: list[
            tuple[str, str, tuple[int, int, int, int, int]]
        ] = []
        for candidate in final_paths:
            candidate_metadata = _assert_no_reparse(candidate, label)
            if stat.S_ISDIR(candidate_metadata.st_mode):
                kind = "directory"
            elif stat.S_ISREG(candidate_metadata.st_mode):
                kind = "file"
            else:
                raise ValueError(f"{label} contains an invalid entry")
            final_children.append(
                (kind, candidate.name, _metadata_identity(candidate_metadata))
            )
        directory_final = _assert_no_reparse(directory, label)
        if (
            _metadata_identity(directory_final) != directory_identity
            or final_children != expected_children
        ):
            raise ValueError(f"{label} exact entries changed during final check")
    entries.sort(key=lambda entry: str(entry["path"]).casefold())
    body = {
        "kind": "complete_directory_tree",
        "entries": entries,
    }
    return {**body, "manifest_sha256": _sha256(body)}


def _copy_regular_file_verified(
    source: Path,
    target: Path,
    expected_entry: Mapping[str, Any],
    label: str,
) -> None:
    metadata_before = _assert_no_reparse(source, label)
    if not stat.S_ISREG(metadata_before.st_mode):
        raise ValueError(f"{label} is not a regular file")
    digest = hashlib.sha256()
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as source_handle:
            opened_before = os.fstat(source_handle.fileno())
            if _metadata_identity(opened_before) != _metadata_identity(
                metadata_before
            ):
                raise ValueError(f"{label} changed before copy")
            with target.open("xb") as target_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    written = target_handle.write(chunk)
                    if written != len(chunk):
                        raise OSError(f"{label} short write")
                    size += written
                    digest.update(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            opened_after = os.fstat(source_handle.fileno())
    except OSError as exc:
        raise ValueError(f"{label} copy failed") from exc
    metadata_after = _assert_no_reparse(source, label)
    observed = {"bytes": size, "sha256": digest.hexdigest()}
    expected = {
        "bytes": expected_entry.get("bytes"),
        "sha256": expected_entry.get("sha256"),
    }
    if (
        _metadata_identity(opened_after) != _metadata_identity(opened_before)
        or _metadata_identity(metadata_after) != _metadata_identity(metadata_before)
        or observed != expected
        or _regular_file_manifest(target, f"copied {label}") != expected
    ):
        raise ValueError(f"{label} copy differs")


def _copy_complete_directory_verified(
    source: Path,
    target: Path,
    expected_manifest: Mapping[str, Any],
    label: str,
) -> None:
    if target.exists():
        raise FileExistsError(f"{label} target already exists")
    expected_tree = {
        key: expected_manifest.get(key)
        for key in ("kind", "entries", "manifest_sha256")
    }
    if _complete_directory_manifest(source, label) != expected_tree:
        raise ValueError(f"{label} changed before copy")
    entries_value = expected_manifest.get("entries")
    if not isinstance(entries_value, list):
        raise ValueError(f"{label} manifest is invalid")
    entries = [dict(entry) for entry in entries_value if isinstance(entry, Mapping)]
    if len(entries) != len(entries_value):
        raise ValueError(f"{label} manifest is invalid")
    target.mkdir(parents=True, exist_ok=False)
    directories = sorted(
        (entry for entry in entries if entry.get("kind") == "directory"),
        key=lambda entry: (len(Path(str(entry["path"])).parts), str(entry["path"])),
    )
    files = sorted(
        (entry for entry in entries if entry.get("kind") == "file"),
        key=lambda entry: str(entry["path"]),
    )
    if len(directories) + len(files) != len(entries):
        raise ValueError(f"{label} manifest is invalid")
    for entry in directories:
        relative = Path(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{label} manifest path is invalid")
        source_directory = source / relative
        before = _assert_no_reparse(source_directory, label)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError(f"{label} directory differs")
        (target / relative).mkdir(exist_ok=False)
        after = _assert_no_reparse(source_directory, label)
        if _metadata_identity(after) != _metadata_identity(before):
            raise ValueError(f"{label} directory changed during copy")
    for entry in files:
        relative = Path(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{label} manifest path is invalid")
        _copy_regular_file_verified(
            source / relative,
            target / relative,
            entry,
            f"{label} {relative.as_posix()}",
        )
    if (
        _complete_directory_manifest(source, label) != expected_tree
        or _complete_directory_manifest(target, f"copied {label}")
        != expected_tree
    ):
        raise ValueError(f"{label} changed during copy")


def _universe_bundle_paths(source_root: Path) -> tuple[Path, Path]:
    metadata_relative = Path(
        str(REPLAY_PLAN["inputs"]["audited_pit_universe_path"])
    )
    if (
        metadata_relative.is_absolute()
        or ".." in metadata_relative.parts
        or metadata_relative.name != "metadata.sqlite3"
    ):
        raise ValueError("audited PIT universe metadata path is invalid")
    return metadata_relative.parent, metadata_relative


def _universe_bundle_manifest(source_root: Path) -> dict[str, Any]:
    bundle_relative, metadata_relative = _universe_bundle_paths(source_root)
    bundle_root = source_root / bundle_relative
    entries = _directory_entries(bundle_root, "audited PIT universe bundle")
    if {entry.name for entry in entries} != {
        "manifest.json",
        "metadata.sqlite3",
        "raw",
    }:
        raise ValueError("audited PIT universe bundle layout is invalid")
    metadata_path = source_root / metadata_relative
    manifest_path = bundle_root / "manifest.json"
    raw_root = bundle_root / "raw"
    for path, expected_mode, label in (
        (metadata_path, stat.S_ISREG, "audited PIT universe metadata"),
        (manifest_path, stat.S_ISREG, "audited PIT universe manifest"),
        (raw_root, stat.S_ISDIR, "audited PIT universe raw root"),
    ):
        if not expected_mode(_assert_no_reparse(path, label).st_mode):
            raise ValueError("audited PIT universe bundle layout is invalid")
    _assert_sqlite_quiescent(metadata_path)
    manifest = _complete_directory_manifest(
        bundle_root,
        "audited PIT universe bundle",
    )
    if not any(
        entry.get("kind") == "file"
        and str(entry.get("path", "")).startswith("raw/")
        for entry in manifest["entries"]
    ):
        raise ValueError("audited PIT universe raw tree is empty")
    return {
        **manifest,
        "bundle_relative_path": bundle_relative.as_posix(),
        "metadata_relative_path": metadata_relative.as_posix(),
    }


def _copy_frozen_inputs(
    source_root: Path,
    data_root: Path,
    *,
    expected_formal_attestation: Mapping[str, Any],
    expected_universe_bundle_manifest_sha256: str,
) -> dict[str, Any]:
    inputs = REPLAY_PLAN["inputs"]
    formal_attestation = _observed_frozen_attestation(source_root)
    if formal_attestation != dict(expected_formal_attestation):
        raise ValueError("formal frozen input changed before copy")
    expected_bundle_sha256 = _require_sha256(
        expected_universe_bundle_manifest_sha256,
        "audited PIT universe bundle manifest",
    )
    universe_bundle_before = _universe_bundle_manifest(source_root)
    if universe_bundle_before["manifest_sha256"] != expected_bundle_sha256:
        raise ValueError("audited PIT universe bundle manifest drifted")
    bundle_relative, metadata_relative = _universe_bundle_paths(source_root)
    plan = (
        ("audited_pit_universe_bundle_root", bundle_relative, "directory_tree"),
        (
            "temporal_contract_path",
            Path(str(inputs["temporal_contract_path"])),
            "file",
        ),
        (
            "current_pool_development_audit_path",
            Path(str(inputs["current_pool_development_audit_path"])),
            "file",
        ),
        (
            "security_code_transition_evidence_root",
            Path(str(inputs["security_code_transition_evidence_root"])),
            "directory_tree",
        ),
    )
    source_before: dict[str, dict[str, Any]] = {}
    target_manifests: dict[str, dict[str, Any]] = {}
    for field, relative, kind in plan:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("frozen input path is invalid")
        source = source_root / relative
        target = data_root / relative
        source_before[field] = (
            _universe_bundle_manifest(source_root)
            if field == "audited_pit_universe_bundle_root"
            else _manifest_path(source, f"frozen input {field}")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("frozen input target already exists")
        if field == "audited_pit_universe_bundle_root":
            _copy_complete_directory_verified(
                source,
                target,
                source_before[field],
                "audited PIT universe bundle",
            )
        elif kind == "file":
            shutil.copyfile(source, target)
        else:
            shutil.copytree(source, target, symlinks=True)
        target_manifests[field] = (
            _universe_bundle_manifest(data_root)
            if field == "audited_pit_universe_bundle_root"
            else _manifest_path(
                target,
                f"copied frozen input {field}",
            )
        )
        if target_manifests[field] != source_before[field]:
            raise ValueError("frozen input copy differs")
    source_after = {
        field: (
            _universe_bundle_manifest(source_root)
            if field == "audited_pit_universe_bundle_root"
            else _manifest_path(source_root / relative, field)
        )
        for field, relative, _kind in plan
    }
    _assert_sqlite_quiescent(
        source_root / metadata_relative
    )
    _assert_sqlite_quiescent(
        data_root / metadata_relative
    )
    if source_after != source_before:
        raise ValueError("frozen input changed during copy")
    if _observed_frozen_attestation(source_root) != formal_attestation:
        raise ValueError("formal frozen input changed during copy")
    body = {
        "schema_version": "risk-on-breadth-independent-frozen-input-copy/v2",
        "input_count": 4,
        "manifests": target_manifests,
        "universe_bundle_manifest_sha256": expected_bundle_sha256,
        "formal_frozen_input_attestation_root_sha256": formal_attestation[
            "root_sha256"
        ],
    }
    return {**body, "root_sha256": _sha256(body)}


def _minimal_child_environment(
    temp_dir: Path,
    *,
    python_executable: Path,
    pycache_blocker: Path,
) -> dict[str, str]:
    resolved_temp = temp_dir.resolve()
    resolved_temp.mkdir(parents=True, exist_ok=True)
    system_root_value = (
        os.environ.get("SystemRoot")
        or os.environ.get("SYSTEMROOT")
        or os.environ.get("WINDIR")
    )
    if not system_root_value:
        raise IndependentVerificationError("Windows system root is unavailable")
    system_root = Path(system_root_value)
    python = python_executable.resolve(strict=True)
    blocker = pycache_blocker.resolve(strict=True)
    path_parts = [
        str(python.parent),
        str(system_root / "System32"),
        str(system_root),
    ]
    normalized_paths = list(
        dict.fromkeys(path_parts_value.casefold() for path_parts_value in path_parts)
    )
    frozen_path = [
        next(value for value in path_parts if value.casefold() == normalized)
        for normalized in normalized_paths
    ]
    return {
        "DISABLE_ENV_FILE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "VPS_RUNTIME_ROLE": "local_research",
        "SYSTEMROOT": str(system_root),
        "WINDIR": str(system_root),
        "COMSPEC": str(system_root / "System32" / "cmd.exe"),
        "PATH": os.pathsep.join(frozen_path),
        "PYTHONPYCACHEPREFIX": str(blocker),
        "TEMP": str(resolved_temp),
        "TMP": str(resolved_temp),
    }


def _isolated_replay_command(
    *,
    python_executable: Path,
    code_root: Path,
    site_packages: Path,
    output_dir: Path,
    pycache_blocker: Path,
) -> list[str]:
    return [
        str(python_executable.resolve()),
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={pycache_blocker.resolve()}",
        "-c",
        ISOLATED_REPLAY_DRIVER,
        json.dumps(_jobs_argv(str(output_dir.resolve()))),
        str(code_root.resolve()),
        str(site_packages.resolve()),
    ]


def _formal_command_tokens(
    source_root: Path,
    python_executable: Path,
) -> list[str]:
    python = python_executable.resolve(strict=True)
    site_packages = (python.parent.parent / "Lib/site-packages").resolve(
        strict=True
    )
    blocker = (source_root / PYCACHE_BLOCKER_RELATIVE).resolve(strict=True)
    return [
        str(python),
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={blocker}",
        "-c",
        FORMAL_APP_JOBS_DRIVER,
        json.dumps(
            [str(source_root.resolve(strict=True)), str(site_packages)],
            ensure_ascii=False,
        ),
        *_jobs_argv(RUN_ROOT_RELATIVE.as_posix()),
    ]


def _assert_project_interpreter(source_root: Path) -> Path:
    try:
        expected = (source_root / ".venv/Scripts/python.exe").resolve(strict=True)
        observed = Path(sys.executable).resolve(strict=True)
    except OSError as exc:
        raise IndependentVerificationError(
            "project virtual environment interpreter is unavailable"
        ) from exc
    if observed != expected:
        raise IndependentVerificationError(
            "independent verifier must use the project virtual environment interpreter"
        )
    return expected


def _verified_source_authority(
    source_root: Path,
    expected_binding: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        authority_root = source_root / SOURCE_AUTHORITY_RELATIVE_ROOT
        entries = _directory_entries(authority_root, "source authority root")
        if len(entries) != 1 or not re.fullmatch(r"[0-9a-f]{64}\.json", entries[0].name):
            raise ValueError("source authority is not unique")
        document, authority_raw = _read_launcher_json(
            entries[0],
            "source authority",
        )
        authority_fields = {
            "schema_version",
            "source_commit",
            "source_tree",
            "launcher_git_path",
            "launcher_git_blob_sha256",
            "verifier_git_path",
            "verifier_git_blob_sha256",
            "run_spec_sha256",
            "replay_plan_sha256",
            "strategy_sha256",
            "producer_root_sha256",
            "scope",
            "execution_topology",
            "artifact_sha256",
        }
        unsigned = dict(document)
        artifact_sha256 = _require_sha256(
            unsigned.pop("artifact_sha256", None),
            "source authority artifact",
        )
        source_commit = str(document.get("source_commit") or "").lower()
        source_tree = str(document.get("source_tree") or "").lower()
        execution_commit = str(
            expected_binding.get("execution_commit") or ""
        ).lower()
        relative_path = entries[0].relative_to(source_root).as_posix()
        if (
            set(document) != authority_fields
            or artifact_sha256 != _sha256(unsigned)
            or entries[0].stem != artifact_sha256
            or document.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-source-authority/v1"
            or not HEX_GIT_SHA1.fullmatch(source_commit)
            or not HEX_GIT_SHA1.fullmatch(source_tree)
            or not HEX_GIT_SHA1.fullmatch(execution_commit)
            or document.get("launcher_git_path")
            != "scripts/run_shallow_gbdt_risk_on_breadth_development_1.py"
            or document.get("verifier_git_path")
            != "scripts/verify_shallow_gbdt_risk_on_breadth_development_1.py"
            or document.get("run_spec_sha256") != EXPECTED_RUN_SPEC_SHA256
            or document.get("replay_plan_sha256")
            != EXPECTED_REPLAY_PLAN_SHA256
            or document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
            or document.get("producer_root_sha256")
            != EXPECTED_PRODUCER_ROOT_SHA256
            or document.get("scope")
            != {
                "point_in_time": True,
                "development_only": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "production_authority": False,
                "automatic_trading_authority": False,
            }
            or document.get("execution_topology")
            != {
                "source_commit_is_only_execution_parent": True,
                "execution_commit_change": "add_single_source_authority_only",
            }
            or _git_output(source_root, "rev-list", "--parents", "-n", "1", execution_commit).split()
            != [execution_commit, source_commit]
            or _git_output(source_root, "rev-parse", f"{source_commit}^{{tree}}").lower()
            != source_tree
            or _git_output(
                source_root,
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                source_commit,
                execution_commit,
            ).splitlines()
            != [f"A\t{relative_path}"]
        ):
            raise ValueError("source authority contract drifted")
        launcher_blob = _git_bytes(
            source_root,
            "show",
            f"{source_commit}:{document['launcher_git_path']}",
        )
        verifier_blob = _git_bytes(
            source_root,
            "show",
            f"{source_commit}:{document['verifier_git_path']}",
        )
        authority_blob = _git_bytes(
            source_root,
            "show",
            f"{execution_commit}:{relative_path}",
        )
        if (
            hashlib.sha256(launcher_blob).hexdigest()
            != document.get("launcher_git_blob_sha256")
            or hashlib.sha256(verifier_blob).hexdigest()
            != document.get("verifier_git_blob_sha256")
            or authority_blob != authority_raw
        ):
            raise ValueError("source authority Git blob drifted")
        binding = {
            "schema_version": "formal-source-authority-binding/v1",
            "artifact_sha256": artifact_sha256,
            "relative_path": relative_path,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "execution_commit": execution_commit,
            "launcher_git_blob_sha256": document["launcher_git_blob_sha256"],
            "verifier_git_blob_sha256": document["verifier_git_blob_sha256"],
            "run_spec_sha256": document["run_spec_sha256"],
            "replay_plan_sha256": document["replay_plan_sha256"],
            "strategy_sha256": document["strategy_sha256"],
            "producer_root_sha256": document["producer_root_sha256"],
            "scope": document["scope"],
        }
        if binding != dict(expected_binding):
            raise ValueError("source authority completion binding drifted")
        return binding
    except (OSError, TypeError, ValueError) as exc:
        raise IndependentVerificationError("source authority is invalid") from exc


def _verified_post_run_verifier_amendment(
    source_root: Path,
    *,
    formal_source_authority: Mapping[str, Any],
    formal_completion_sha256: str,
) -> dict[str, Any]:
    try:
        _json_document_size_limit("formal_control")
        authority_root = source_root / VERIFIER_AMENDMENT_RELATIVE_ROOT
        entries = _directory_entries(
            authority_root,
            "post-run verifier amendment authority root",
        )
        if (
            len(entries) != 1
            or not re.fullmatch(r"[0-9a-f]{64}\.json", entries[0].name)
        ):
            raise ValueError("post-run verifier amendment authority is not unique")
        document, authority_raw = _read_launcher_json(
            entries[0],
            "post-run verifier amendment authority",
        )
        authority_fields = {
            "schema_version",
            "formal_source_authority_sha256",
            "formal_execution_commit",
            "formal_completion_sha256",
            "predecessor_verifier_git_blob_sha256",
            "successor_source_commit",
            "successor_source_tree",
            "successor_verifier_git_path",
            "successor_verifier_git_blob_sha256",
            "successor_git_blobs_sha256",
            "json_document_size_policy",
            "json_document_size_policy_sha256",
            "replay_plan_sha256",
            "scope",
            "execution_topology",
            "artifact_sha256",
        }
        unsigned = dict(document)
        artifact_sha256 = _require_sha256(
            unsigned.pop("artifact_sha256", None),
            "post-run verifier amendment artifact",
        )
        formal_authority_sha256 = _require_sha256(
            formal_source_authority.get("artifact_sha256"),
            "formal source authority artifact",
        )
        completion_sha256 = _require_sha256(
            formal_completion_sha256,
            "formal completion artifact",
        )
        predecessor_verifier_sha256 = _require_sha256(
            formal_source_authority.get("verifier_git_blob_sha256"),
            "formal predecessor verifier",
        )
        formal_execution = str(
            formal_source_authority.get("execution_commit") or ""
        ).lower()
        successor_source = str(
            document.get("successor_source_commit") or ""
        ).lower()
        successor_tree = str(
            document.get("successor_source_tree") or ""
        ).lower()
        successor_git_blobs_value = document.get(
            "successor_git_blobs_sha256"
        )
        if (
            not isinstance(successor_git_blobs_value, Mapping)
            or set(successor_git_blobs_value)
            != set(VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS)
        ):
            raise ValueError("post-run verifier successor Git blobs are invalid")
        successor_git_blobs_sha256 = {
            path: _require_sha256(
                successor_git_blobs_value.get(path),
                f"post-run verifier successor Git blob {path}",
            )
            for path in VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS
        }
        amendment_execution = EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT
        relative_path = entries[0].relative_to(source_root).as_posix()
        expected_successor_changes = {
            f"M\t{path}" for path in VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS
        }
        if (
            set(document) != authority_fields
            or artifact_sha256 != _sha256(unsigned)
            or entries[0].stem != artifact_sha256
            or document.get("schema_version") != VERIFIER_AMENDMENT_SCHEMA
            or not HEX_GIT_SHA1.fullmatch(formal_execution)
            or not HEX_GIT_SHA1.fullmatch(successor_source)
            or not HEX_GIT_SHA1.fullmatch(successor_tree)
            or not HEX_GIT_SHA1.fullmatch(amendment_execution)
            or document.get("formal_source_authority_sha256")
            != formal_authority_sha256
            or document.get("formal_execution_commit") != formal_execution
            or document.get("formal_completion_sha256") != completion_sha256
            or document.get("predecessor_verifier_git_blob_sha256")
            != predecessor_verifier_sha256
            or document.get("successor_verifier_git_path")
            != VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
            or document.get("successor_git_blobs_sha256")
            != successor_git_blobs_sha256
            or document.get("successor_verifier_git_blob_sha256")
            != successor_git_blobs_sha256[
                VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
            ]
            or document.get("json_document_size_policy")
            != JSON_DOCUMENT_SIZE_POLICY
            or document.get("json_document_size_policy_sha256")
            != EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
            or document.get("replay_plan_sha256")
            != EXPECTED_REPLAY_PLAN_SHA256
            or document.get("scope") != VERIFIER_AMENDMENT_SCOPE
            or document.get("execution_topology")
            != VERIFIER_AMENDMENT_TOPOLOGY
            or artifact_sha256
            != EXPECTED_VERIFIER_AMENDMENT_ARTIFACT_SHA256
            or _git_output(
                source_root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                amendment_execution,
            ).split()
            != [amendment_execution, successor_source]
            or _git_output(
                source_root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                successor_source,
            ).split()
            != [successor_source, formal_execution]
            or _git_output(
                source_root,
                "rev-parse",
                f"{successor_source}^{{tree}}",
            ).lower()
            != successor_tree
            or set(
                _git_output(
                    source_root,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    formal_execution,
                    successor_source,
                ).splitlines()
            )
            != expected_successor_changes
            or _git_output(
                source_root,
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                successor_source,
                amendment_execution,
            ).splitlines()
            != [f"A\t{relative_path}"]
        ):
            raise ValueError("post-run verifier amendment authority drifted")
        successor_git_blobs = {
            path: _git_bytes(
                source_root,
                "show",
                f"{successor_source}:{path}",
            )
            for path in VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS
        }
        authority_blob = _git_bytes(
            source_root,
            "show",
            f"{amendment_execution}:{relative_path}",
        )
        try:
            attribute_lines = successor_git_blobs[".gitattributes"].decode(
                "utf-8"
            ).splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("post-run verifier attributes are invalid") from exc
        observed_successor_git_blobs_sha256 = {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in successor_git_blobs.items()
        }
        successor_verifier_sha256 = observed_successor_git_blobs_sha256[
            VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
        ]
        if (
            observed_successor_git_blobs_sha256
            != successor_git_blobs_sha256
            or attribute_lines.count(VERIFIER_AMENDMENT_GIT_ATTRIBUTES_RULE) != 1
            or authority_blob != authority_raw
        ):
            raise ValueError("post-run verifier amendment Git blob drifted")
        return {
            "schema_version": (
                "formal-post-run-independent-verifier-amendment-binding/v1"
            ),
            "artifact_sha256": artifact_sha256,
            "relative_path": relative_path,
            "formal_source_authority_sha256": formal_authority_sha256,
            "formal_execution_commit": formal_execution,
            "formal_completion_sha256": completion_sha256,
            "predecessor_verifier_git_blob_sha256": (
                predecessor_verifier_sha256
            ),
            "successor_source_commit": successor_source,
            "successor_source_tree": successor_tree,
            "execution_commit": amendment_execution,
            "successor_verifier_git_blob_sha256": successor_verifier_sha256,
            "successor_git_blobs_sha256": successor_git_blobs_sha256,
            "json_document_size_policy": JSON_DOCUMENT_SIZE_POLICY,
            "json_document_size_policy_sha256": (
                EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
            ),
            "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
            "scope": VERIFIER_AMENDMENT_SCOPE,
        }
    except IndependentVerificationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise IndependentVerificationError(
            "post-run verifier amendment authority is invalid"
        ) from exc


def _require_absent(path: Path, label: str) -> str:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise ValueError(f"{label} state is unreadable") from exc
    else:
        raise ValueError(f"{label} must be absent")


def _verified_original_failed_attempt(run_root: Path) -> dict[str, Any]:
    try:
        claim_path = run_root / CLAIM_NAME
        status_path = run_root / STATUS_NAME
        claim, claim_raw = _read_launcher_json(
            claim_path,
            "original failed independent verification claim",
            fields=ORIGINAL_FAILED_CLAIM_FIELDS,
        )
        status, status_raw = _read_launcher_json(
            status_path,
            "original failed independent verification status",
            fields=ORIGINAL_FAILED_STATUS_FIELDS,
        )
        claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
        status_sha256 = hashlib.sha256(status_raw).hexdigest()
        verification_state = _require_absent(
            run_root / VERIFICATION_ROOT_NAME,
            "original independent verification artifacts",
        )
        receipt_state = _require_absent(
            run_root / RECEIPT_ROOT_NAME,
            "original independent verification receipts",
        )
        if (
            claim_sha256 != EXPECTED_FAILED_CLAIM_SHA256
            or status_sha256 != EXPECTED_FAILED_STATUS_SHA256
            or claim.get("development_only") is not True
            or claim.get("embargo_consumed") is not False
            or claim.get("final_oos_consumed") is not False
            or claim.get("production_authority") is not False
            or claim.get("automatic_trading_authority") is not False
            or status.get("schema_version") != STATUS_SCHEMA
            or status.get("status") != "failed"
            or status.get("stage") != "failed"
            or status.get("verified") is not False
            or status.get("receipt_sha256") is not None
            or status.get("claim_sha256") != claim_sha256
            or status.get("receipt_path") is not None
            or status.get("error_type") != "IndependentVerificationError"
            or status.get("development_only") is not True
            or status.get("development_statistical_interpretation_allowed")
            is not False
            or status.get("profile_registration_authority") is not False
            or status.get("production_recommendation_authority") is not False
            or status.get("automatic_trading_authority") is not False
            or status.get("production_authority") is not False
            or status.get("embargo_consumed") is not False
            or status.get("final_oos_consumed") is not False
        ):
            raise ValueError("original failed independent verification drifted")
        return {
            "claim_path": CLAIM_NAME,
            "claim_sha256": claim_sha256,
            "status_path": STATUS_NAME,
            "status_sha256": status_sha256,
            "verification_artifacts_state": verification_state,
            "receipts_state": receipt_state,
            "status": "failed",
            "stage": "failed",
            "verified": False,
            "error_type": "IndependentVerificationError",
        }
    except IndependentVerificationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise IndependentVerificationError(
            "original failed attempt evidence is invalid"
        ) from exc


def _verified_post_failure_retry_authority(
    source_root: Path,
    *,
    original_amendment: Mapping[str, Any],
    original_failed_attempt: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        authority_root = source_root / RETRY_AUTHORITY_RELATIVE_ROOT
        entries = _directory_entries(
            authority_root,
            "post-failure verifier retry authority root",
        )
        if (
            len(entries) != 1
            or not re.fullmatch(r"[0-9a-f]{64}\.json", entries[0].name)
        ):
            raise ValueError("post-failure verifier retry authority is not unique")
        document, authority_raw = _read_launcher_json(
            entries[0],
            "post-failure verifier retry authority",
        )
        authority_fields = {
            "schema_version",
            "retry_id",
            "original_amendment_execution_commit",
            "original_amendment_artifact_sha256",
            "original_failed_claim_path",
            "original_failed_claim_sha256",
            "original_failed_status_path",
            "original_failed_status_sha256",
            "original_verification_artifacts_state",
            "original_receipts_state",
            "universe_bundle_relative_path",
            "universe_metadata_relative_path",
            "universe_bundle_manifest_sha256",
            "retry_source_commit",
            "retry_source_tree",
            "retry_verifier_git_path",
            "retry_verifier_git_blob_sha256",
            "retry_git_blobs_sha256",
            "replay_plan_sha256",
            "failure_classification",
            "scope",
            "execution_topology",
            "artifact_sha256",
        }
        unsigned = dict(document)
        artifact_sha256 = _require_sha256(
            unsigned.pop("artifact_sha256", None),
            "post-failure verifier retry authority artifact",
        )
        original_execution = str(
            original_amendment.get("execution_commit") or ""
        ).lower()
        original_artifact = _require_sha256(
            original_amendment.get("artifact_sha256"),
            "original verifier amendment artifact",
        )
        retry_source = str(document.get("retry_source_commit") or "").lower()
        retry_tree = str(document.get("retry_source_tree") or "").lower()
        retry_execution = _git_output(source_root, "rev-parse", "HEAD").lower()
        retry_blobs_value = document.get("retry_git_blobs_sha256")
        if (
            not isinstance(retry_blobs_value, Mapping)
            or set(retry_blobs_value) != set(RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS)
        ):
            raise ValueError("post-failure verifier retry Git blobs are invalid")
        retry_blobs_sha256 = {
            path: _require_sha256(
                retry_blobs_value.get(path),
                f"post-failure verifier retry Git blob {path}",
            )
            for path in RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS
        }
        bundle_manifest = _universe_bundle_manifest(source_root)
        relative_path = entries[0].relative_to(source_root).as_posix()
        expected_retry_changes = {
            f"M\t{path}" for path in RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS
        }
        if (
            set(document) != authority_fields
            or artifact_sha256 != _sha256(unsigned)
            or entries[0].stem != artifact_sha256
            or document.get("schema_version") != RETRY_AUTHORITY_SCHEMA
            or document.get("retry_id") != "retry_1"
            or original_execution
            != EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT
            or document.get("original_amendment_execution_commit")
            != original_execution
            or document.get("original_amendment_artifact_sha256")
            != original_artifact
            or document.get("original_failed_claim_path")
            != original_failed_attempt.get("claim_path")
            or document.get("original_failed_claim_sha256")
            != original_failed_attempt.get("claim_sha256")
            or document.get("original_failed_status_path")
            != original_failed_attempt.get("status_path")
            or document.get("original_failed_status_sha256")
            != original_failed_attempt.get("status_sha256")
            or document.get("original_verification_artifacts_state")
            != original_failed_attempt.get("verification_artifacts_state")
            or document.get("original_receipts_state")
            != original_failed_attempt.get("receipts_state")
            or document.get("universe_bundle_relative_path")
            != bundle_manifest.get("bundle_relative_path")
            or document.get("universe_metadata_relative_path")
            != bundle_manifest.get("metadata_relative_path")
            or document.get("universe_bundle_manifest_sha256")
            != bundle_manifest.get("manifest_sha256")
            or not HEX_GIT_SHA1.fullmatch(retry_source)
            or not HEX_GIT_SHA1.fullmatch(retry_tree)
            or not HEX_GIT_SHA1.fullmatch(retry_execution)
            or document.get("retry_verifier_git_path")
            != VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
            or document.get("retry_verifier_git_blob_sha256")
            != retry_blobs_sha256[VERIFIER_AMENDMENT_VERIFIER_GIT_PATH]
            or document.get("retry_git_blobs_sha256") != retry_blobs_sha256
            or document.get("replay_plan_sha256")
            != EXPECTED_REPLAY_PLAN_SHA256
            or document.get("failure_classification")
            != RETRY_FAILURE_CLASSIFICATION
            or document.get("scope") != RETRY_AUTHORITY_SCOPE
            or document.get("execution_topology") != RETRY_AUTHORITY_TOPOLOGY
            or _git_output(
                source_root,
                "status",
                "--porcelain",
                "--untracked-files=all",
            )
            or _git_output(
                source_root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                retry_execution,
            ).split()
            != [retry_execution, retry_source]
            or _git_output(
                source_root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                retry_source,
            ).split()
            != [retry_source, original_execution]
            or _git_output(
                source_root,
                "rev-parse",
                f"{retry_source}^{{tree}}",
            ).lower()
            != retry_tree
            or set(
                _git_output(
                    source_root,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    original_execution,
                    retry_source,
                ).splitlines()
            )
            != expected_retry_changes
            or _git_output(
                source_root,
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                retry_source,
                retry_execution,
            ).splitlines()
            != [f"A\t{relative_path}"]
        ):
            raise ValueError("post-failure verifier retry authority drifted")
        retry_blobs = {
            path: _git_bytes(source_root, "show", f"{retry_source}:{path}")
            for path in RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS
        }
        observed_retry_blobs_sha256 = {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in retry_blobs.items()
        }
        live_retry_blobs = {
            path: (
                SCRIPT_PATH
                if path == VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
                else source_root / path
            )
            .read_bytes()
            .replace(b"\r\n", b"\n")
            for path in RETRY_AUTHORITY_SUCCESSOR_GIT_PATHS
        }
        authority_blob = _git_bytes(
            source_root,
            "show",
            f"{retry_execution}:{relative_path}",
        )
        attribute_lines = retry_blobs[".gitattributes"].decode("utf-8").splitlines()
        if (
            observed_retry_blobs_sha256 != retry_blobs_sha256
            or retry_blobs != live_retry_blobs
            or attribute_lines.count(RETRY_AUTHORITY_GIT_ATTRIBUTES_RULE) != 1
            or authority_blob != authority_raw
        ):
            raise ValueError("post-failure verifier retry Git blob drifted")
        return {
            "schema_version": (
                "formal-post-failure-independent-verifier-retry-binding/v1"
            ),
            "artifact_sha256": artifact_sha256,
            "relative_path": relative_path,
            "original_amendment_execution_commit": original_execution,
            "original_amendment_artifact_sha256": original_artifact,
            "original_failed_claim_sha256": original_failed_attempt[
                "claim_sha256"
            ],
            "original_failed_status_sha256": original_failed_attempt[
                "status_sha256"
            ],
            "universe_bundle_relative_path": bundle_manifest[
                "bundle_relative_path"
            ],
            "universe_metadata_relative_path": bundle_manifest[
                "metadata_relative_path"
            ],
            "universe_bundle_manifest_sha256": bundle_manifest[
                "manifest_sha256"
            ],
            "retry_source_commit": retry_source,
            "retry_source_tree": retry_tree,
            "execution_commit": retry_execution,
            "retry_verifier_git_blob_sha256": retry_blobs_sha256[
                VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
            ],
            "retry_git_blobs_sha256": retry_blobs_sha256,
            "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
            "failure_classification": RETRY_FAILURE_CLASSIFICATION,
            "scope": RETRY_AUTHORITY_SCOPE,
        }
    except IndependentVerificationError:
        raise
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise IndependentVerificationError(
            "post-failure verifier retry authority is invalid"
        ) from exc


def _runtime_distribution_sha256(attestation: object, label: str) -> str:
    if not isinstance(attestation, Mapping):
        raise ValueError(f"{label} is invalid")
    value = dict(attestation)
    root_sha256 = _require_sha256(value.pop("root_sha256", None), label)
    distributions = value.get("distributions")
    environment = value.get("environment")
    if (
        root_sha256 != _sha256(value)
        or value.get("schema_version") != "formal-python-runtime-attestation/v1"
        or not isinstance(distributions, list)
        or not isinstance(environment, Mapping)
        or environment.get("inherit_parent_environment") is not False
    ):
        raise ValueError(f"{label} is invalid")
    xgboost = [
        item
        for item in distributions
        if isinstance(item, Mapping) and item.get("name") == "xgboost"
    ]
    if len(xgboost) != 1:
        raise ValueError(f"{label} xgboost distribution is invalid")
    return _require_sha256(xgboost[0].get("files_sha256"), label)


def _formal_runtime_history(
    preflight: Mapping[str, Any],
    postflight: Mapping[str, Any],
) -> dict[str, Any]:
    pre_sha256 = _runtime_distribution_sha256(
        preflight.get("runtime_attestation"),
        "formal preflight runtime",
    )
    post_sha256 = _runtime_distribution_sha256(
        postflight.get("runtime_attestation"),
        "formal postflight runtime",
    )
    history = {
        "schema_version": FORMAL_RUNTIME_HISTORY_SCHEMA,
        "preflight_xgboost_distribution_files_sha256": pre_sha256,
        "postflight_xgboost_distribution_files_sha256": post_sha256,
        "immutable_inputs_unchanged": preflight == postflight,
    }
    return _verified_formal_runtime_history(history)


def _observed_frozen_attestation(source_root: Path) -> dict[str, Any]:
    inputs = REPLAY_PLAN["inputs"]
    file_fields = (
        "audited_pit_universe_path",
        "temporal_contract_path",
        "current_pool_development_audit_path",
    )
    files: list[dict[str, Any]] = []
    for field in file_fields:
        relative = Path(str(inputs[field]))
        path = source_root / relative
        metadata = _assert_no_reparse(path, f"formal frozen input {field}")
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("formal frozen input is invalid")
        files.append(
            {
                "path": relative.as_posix(),
                "bytes": metadata.st_size,
                "sha256": _file_sha256(path),
            }
        )
    _assert_sqlite_quiescent(
        source_root / Path(str(inputs["audited_pit_universe_path"]))
    )
    transition_relative = Path(
        str(inputs["security_code_transition_evidence_root"])
    )
    transition_root = source_root / transition_relative
    transition_metadata = _assert_no_reparse(
        transition_root,
        "formal transition frozen input",
    )
    if not stat.S_ISDIR(transition_metadata.st_mode):
        raise ValueError("formal transition frozen input is invalid")
    transition_count = 0
    for path in sorted(transition_root.rglob("*"), key=lambda item: item.as_posix()):
        metadata = _assert_no_reparse(path, "formal transition frozen input")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("formal transition frozen input is invalid")
        transition_count += 1
        files.append(
            {
                "path": path.relative_to(source_root).as_posix(),
                "bytes": metadata.st_size,
                "sha256": _file_sha256(path),
            }
        )
    if transition_count == 0:
        raise ValueError("formal transition frozen input is empty")
    files.sort(key=lambda item: item["path"].casefold())
    body = {
        "schema_version": "formal-frozen-input-attestation/v1",
        "algorithm": "sha256",
        "files": files,
    }
    return {**body, "root_sha256": _sha256(body)}


def _assert_log_reference(
    value: object,
    path: Path,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} reference is invalid")
    metadata = _assert_no_reparse(path, label)
    claimed_path = Path(str(value.get("path") or ""))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or claimed_path.resolve() != path.resolve()
        or value.get("bytes") != metadata.st_size
        or value.get("sha256") != _file_sha256(path)
    ):
        raise ValueError(f"{label} reference drifted")
    return {
        "path": path.name,
        "bytes": metadata.st_size,
        "sha256": value["sha256"],
    }


def _load_formal_inputs(source_root_value: str | Path) -> dict[str, Any]:
    try:
        source_root = Path(source_root_value).resolve(strict=True)
        run_root = source_root / RUN_ROOT_RELATIVE
        run_metadata = _assert_no_reparse(run_root, "formal run root")
        if not stat.S_ISDIR(run_metadata.st_mode):
            raise ValueError("formal run root is invalid")
        claim_fields = frozenset(
            {
                "schema_version",
                "started_at_utc",
                "run_spec_sha256",
                "execution_commit",
                "source_commit",
                "source_authority_sha256",
                "output_dir",
                "preflight_sha256",
                "max_formal_attempts",
                "development_only",
                "embargo_consumed",
                "final_oos_consumed",
                "production_authority",
                "automatic_trading_authority",
            }
        )
        terminal_fields = frozenset(
            {
                "schema_version",
                "status",
                "finished_at_utc",
                "attempt_claim_sha256",
                "completion_sha256",
                "failure_sha256",
                "error_type",
                "development_only",
                "embargo_consumed",
                "final_oos_consumed",
                "profile_registration_authority",
                "production_recommendation_authority",
                "automatic_trading_authority",
                "production_authority",
            }
        )
        completion_fields = frozenset(
            {
                "schema_version",
                "started_at_utc",
                "finished_at_utc",
                "classification",
                "preflight",
                "post_run_preflight",
                "immutable_inputs_unchanged",
                "launch_receipt_sha256",
                "attempt_claim_sha256",
                "resource_receipt",
                "launcher_error_type",
                "progress",
                "result_artifact",
                "runtime_verification",
                "result_available",
                "artifact_content_addressed",
                "runtime_verification_content_addressed",
                "independent_verification_complete",
                "statistical_interpretation_allowed",
                "profile_registration_authority",
                "production_recommendation_authority",
                "automatic_trading_authority",
                "development_only",
                "embargo_consumed",
                "final_oos_consumed",
                "production_authority",
            }
        )
        preflight_fields = frozenset(
            {
                "schema_version",
                "observed_at_utc",
                "git_commit",
                "source_authority",
                "strategy_sha256",
                "producer_binding",
                "current_pool_audit_binding",
                "formal_launcher_sha256",
                "formal_launcher_git_blob_sha256",
                "independent_verifier_git_blob_sha256",
                "replay_plan_sha256",
                "run_spec_sha256",
                "frozen_input_attestation",
                "runtime_attestation",
                "run_spec",
                "attempt_claim_sha256",
                "statistical_result_available",
            }
        )
        launch_fields = frozenset(
            {
                "schema_version",
                "started_at_utc",
                "preflight_sha256",
                "executable",
                "arguments",
                "command_sha256",
                "execution_contract",
                "runtime_environment_attestation_sha256",
                "memory_policy",
                "process_tree_policy",
                "development_only",
                "embargo_consumed",
                "final_oos_consumed",
                "production_authority",
                "automatic_trading_authority",
            }
        )
        resource_fields = frozenset(
            {
                "schema_version",
                "pid",
                "pid_role",
                "started_at_utc",
                "finished_at_utc",
                "exit_code",
                "command_sha256",
                "argument_count",
                "cwd",
                "shell",
                "stdin_closed",
                "memory_limit_enforced",
                "child_reaped",
                "job_object_assigned",
                "process_tree_drained",
                "process_tree_drain_verification",
                "stdout",
                "stderr",
            }
        )
        claim, claim_raw = _read_launcher_json(
            source_root / ATTEMPT_RELATIVE,
            "formal attempt claim",
            fields=claim_fields,
        )
        completion, completion_raw = _read_launcher_json(
            run_root / COMPLETION_NAME,
            "formal completion",
            fields=completion_fields,
        )
        terminal, terminal_raw = _read_launcher_json(
            source_root / TERMINAL_RELATIVE,
            "formal attempt terminal",
            fields=terminal_fields,
        )
        preflight_document, preflight_raw = _read_launcher_json(
            run_root / PREFLIGHT_NAME,
            "formal preflight",
            fields=preflight_fields,
        )
        launch, launch_raw = _read_launcher_json(
            run_root / LAUNCH_NAME,
            "formal launch",
            fields=launch_fields,
        )
        resource, resource_raw = _read_launcher_json(
            run_root / RESOURCE_NAME,
            "formal resource receipt",
            fields=resource_fields,
        )
        claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
        completion_sha256 = hashlib.sha256(completion_raw).hexdigest()
        wrapper_fields = {
            "schema_version",
            "observed_at_utc",
            "run_spec",
            "attempt_claim_sha256",
            "statistical_result_available",
        }
        preflight_core = {
            key: value
            for key, value in preflight_document.items()
            if key not in wrapper_fields
        }
        postflight = completion.get("post_run_preflight")
        if not isinstance(postflight, Mapping):
            raise ValueError("formal postflight is invalid")
        source_authority = preflight_core.get("source_authority")
        producer = preflight_core.get("producer_binding")
        current_pool = preflight_core.get("current_pool_audit_binding")
        frozen_attestation = preflight_core.get("frozen_input_attestation")
        frozen_unsigned = (
            dict(frozen_attestation) if isinstance(frozen_attestation, Mapping) else {}
        )
        frozen_root = frozen_unsigned.pop("root_sha256", None)
        formal_python = (source_root / ".venv/Scripts/python.exe").resolve(
            strict=True
        )
        formal_command = _formal_command_tokens(source_root, formal_python)
        formal_command_sha256 = _sha256({"tokens": formal_command})
        if (
            claim.get("schema_version") != "formal-single-attempt-claim/v1"
            or claim.get("run_spec_sha256") != EXPECTED_RUN_SPEC_SHA256
            or claim.get("output_dir") != RUN_ROOT_RELATIVE.as_posix()
            or claim.get("preflight_sha256") != _sha256(preflight_core)
            or claim.get("max_formal_attempts") != 1
            or any(
                claim.get(field) is not expected
                for field, expected in (
                    ("development_only", True),
                    ("embargo_consumed", False),
                    ("final_oos_consumed", False),
                    ("production_authority", False),
                    ("automatic_trading_authority", False),
                )
            )
            or completion.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-completion/v1"
            or completion.get("classification")
            != "completed_result_pending_independent_verification"
            or completion.get("preflight") != preflight_core
            or dict(postflight) != preflight_core
            or completion.get("immutable_inputs_unchanged") is not True
            or completion.get("attempt_claim_sha256") != claim_sha256
            or completion.get("launch_receipt_sha256")
            != hashlib.sha256(launch_raw).hexdigest()
            or completion.get("launcher_error_type") is not None
            or any(
                completion.get(field) is not expected
                for field, expected in (
                    ("result_available", True),
                    ("artifact_content_addressed", True),
                    ("runtime_verification_content_addressed", True),
                    ("independent_verification_complete", False),
                    ("statistical_interpretation_allowed", False),
                    ("profile_registration_authority", False),
                    ("production_recommendation_authority", False),
                    ("automatic_trading_authority", False),
                    ("development_only", True),
                    ("embargo_consumed", False),
                    ("final_oos_consumed", False),
                    ("production_authority", False),
                )
            )
            or terminal.get("schema_version") != "formal-single-attempt-terminal/v1"
            or terminal.get("status") != "completed"
            or terminal.get("attempt_claim_sha256") != claim_sha256
            or terminal.get("completion_sha256") != completion_sha256
            or terminal.get("failure_sha256") is not None
            or terminal.get("error_type") is not None
            or any(
                terminal.get(field) is not expected
                for field, expected in (
                    ("development_only", True),
                    ("embargo_consumed", False),
                    ("final_oos_consumed", False),
                    ("profile_registration_authority", False),
                    ("production_recommendation_authority", False),
                    ("automatic_trading_authority", False),
                    ("production_authority", False),
                )
            )
            or (run_root / FAILURE_NAME).exists()
            or preflight_document.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-preflight/v1"
            or preflight_document.get("attempt_claim_sha256") != claim_sha256
            or preflight_document.get("statistical_result_available") is not False
            or _sha256(preflight_document.get("run_spec"))
            != EXPECTED_RUN_SPEC_SHA256
            or preflight_core.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
            or preflight_core.get("run_spec_sha256") != EXPECTED_RUN_SPEC_SHA256
            or preflight_core.get("replay_plan_sha256")
            != EXPECTED_REPLAY_PLAN_SHA256
            or not isinstance(producer, Mapping)
            or _verified_producer_binding(producer) != dict(producer)
            or not isinstance(current_pool, Mapping)
            or set(current_pool) != {"canonical_sha256", "allowed_symbol_count"}
            or current_pool.get("canonical_sha256")
            != REPLAY_PLAN["inputs"]["expected_current_pool_development_audit_sha256"]
            or isinstance(current_pool.get("allowed_symbol_count"), bool)
            or not isinstance(current_pool.get("allowed_symbol_count"), int)
            or current_pool["allowed_symbol_count"] <= 0
            or frozen_unsigned.get("schema_version")
            != "formal-frozen-input-attestation/v1"
            or frozen_unsigned.get("algorithm") != "sha256"
            or not isinstance(frozen_unsigned.get("files"), list)
            or frozen_root != _sha256(frozen_unsigned)
            or dict(frozen_attestation) != _observed_frozen_attestation(source_root)
            or preflight_core.get("formal_launcher_sha256")
            != _file_sha256(
                source_root
                / "scripts/run_shallow_gbdt_risk_on_breadth_development_1.py"
            )
            or launch.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-launch/v1"
            or launch.get("preflight_sha256") != _sha256(preflight_core)
            or launch.get("executable") != str(formal_python)
            or launch.get("arguments")
            != _jobs_argv(RUN_ROOT_RELATIVE.as_posix())
            or launch.get("command_sha256") != formal_command_sha256
            or launch.get("execution_contract")
            != {
                "interpreter_flags": ["-S", "-B", "-P"],
                "entrypoint": "runpy.run_module-app.jobs",
                "jobs_argument_prefix": ["-m", "app.jobs"],
                "sys_path": ["workspace", "venv-site-packages"],
                "pycache_prefix_from_command_line": True,
            }
            or launch.get("memory_policy") != "unbounded"
            or launch.get("process_tree_policy")
            != "windows_job_object_assigned_and_drained"
            or launch.get("runtime_environment_attestation_sha256")
            != preflight_core.get("runtime_attestation", {}).get("root_sha256")
            or any(
                launch.get(field) is not expected
                for field, expected in (
                    ("development_only", True),
                    ("embargo_consumed", False),
                    ("final_oos_consumed", False),
                    ("production_authority", False),
                    ("automatic_trading_authority", False),
                )
            )
            or resource.get("schema_version")
            != "research-unbounded-job-object-command-receipt/v1"
            or isinstance(resource.get("pid"), bool)
            or not isinstance(resource.get("pid"), int)
            or resource["pid"] <= 0
            or resource.get("pid_role") != "gated_bootstrap_supervisor"
            or resource.get("exit_code") != 0
            or resource.get("command_sha256") != formal_command_sha256
            or resource.get("argument_count") != len(formal_command)
            or Path(str(resource.get("cwd") or "")).resolve() != source_root
            or resource.get("shell") is not False
            or resource.get("stdin_closed") is not True
            or resource.get("memory_limit_enforced") is not False
            or resource.get("child_reaped") is not True
            or resource.get("job_object_assigned") is not True
            or resource.get("process_tree_drained") is not True
            or resource.get("process_tree_drain_verification")
            != "windows_job_object_active_process_count_zero"
        ):
            raise ValueError("formal completion chain drifted")
        if not isinstance(source_authority, Mapping):
            raise ValueError("formal source authority binding is invalid")
        source_authority = _verified_source_authority(source_root, source_authority)
        if (
            claim.get("execution_commit") != source_authority["execution_commit"]
            or claim.get("source_commit") != source_authority["source_commit"]
            or claim.get("source_authority_sha256")
            != source_authority["artifact_sha256"]
            or preflight_core.get("git_commit")
            != source_authority["execution_commit"]
            or preflight_core.get("formal_launcher_git_blob_sha256")
            != source_authority["launcher_git_blob_sha256"]
            or preflight_core.get("independent_verifier_git_blob_sha256")
            != source_authority["verifier_git_blob_sha256"]
        ):
            raise ValueError("formal source authority chain drifted")
        verifier_amendment = _verified_post_run_verifier_amendment(
            source_root,
            formal_source_authority=source_authority,
            formal_completion_sha256=completion_sha256,
        )
        resource_reference = completion.get("resource_receipt")
        if (
            not isinstance(resource_reference, Mapping)
            or set(resource_reference)
            != {
                "path",
                "sha256",
                "exit_code",
                "memory_limit_enforced",
                "job_object_assigned",
                "process_tree_drained",
            }
            or resource_reference.get("path") != RESOURCE_NAME
            or resource_reference.get("sha256")
            != hashlib.sha256(resource_raw).hexdigest()
            or resource_reference.get("exit_code") != 0
            or resource_reference.get("memory_limit_enforced") is not False
            or resource_reference.get("job_object_assigned") is not True
            or resource_reference.get("process_tree_drained") is not True
        ):
            raise ValueError("formal resource completion reference drifted")
        formal_stdout = _assert_log_reference(
            resource.get("stdout"),
            run_root / FORMAL_STDOUT_NAME,
            "formal stdout",
        )
        formal_stderr = _assert_log_reference(
            resource.get("stderr"),
            run_root / FORMAL_STDERR_NAME,
            "formal stderr",
        )
        progress = completion.get("progress")
        main_reference = completion.get("result_artifact")
        runtime_reference = completion.get("runtime_verification")
        if (
            not isinstance(progress, Mapping)
            or set(progress) != {"schema_version", "stage", "artifact_sha256"}
            or progress.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-replay-progress/v1"
            or progress.get("stage") != "completed"
            or not isinstance(main_reference, Mapping)
            or set(main_reference)
            != {
                "path",
                "file_sha256",
                "canonical_artifact_sha256",
                "embedded_artifact_sha256",
                "sidecar_artifact_sha256",
            }
            or not isinstance(runtime_reference, Mapping)
            or set(runtime_reference)
            != {
                "path",
                "file_sha256",
                "canonical_artifact_sha256",
                "embedded_artifact_sha256",
                "schema_version",
                "main_artifact_sha256",
                "market_breadth_feature_binding_receipt_sha256",
            }
        ):
            raise ValueError("formal result completion reference drifted")
        runtime_path = run_root / "verifications" / str(runtime_reference["path"])
        runtime_document = _read_json_object(
            runtime_path,
            "formal runtime verification",
            document_class="runtime_verification",
        )
        result_bundle = _load_content_addressed_result_bundle(
            run_root,
            runtime_verification=runtime_document,
        )
        main_path = result_bundle["main_artifact_path"]
        runtime_artifact_sha256 = runtime_document.get("artifact_sha256")
        if (
            progress.get("artifact_sha256")
            != result_bundle["main_artifact_sha256"]
            or main_reference.get("path") != main_path.name
            or main_reference.get("canonical_artifact_sha256")
            != result_bundle["main_artifact_sha256"]
            or main_reference.get("embedded_artifact_sha256")
            != result_bundle["main_artifact_sha256"]
            or main_reference.get("file_sha256") != _file_sha256(main_path)
            or main_reference.get("sidecar_artifact_sha256")
            != result_bundle["sidecar_artifact_sha256"]
            or runtime_reference.get("path") != runtime_path.name
            or runtime_reference.get("canonical_artifact_sha256")
            != runtime_artifact_sha256
            or runtime_reference.get("embedded_artifact_sha256")
            != runtime_artifact_sha256
            or runtime_reference.get("file_sha256") != _file_sha256(runtime_path)
            or runtime_reference.get("schema_version")
            != EXPECTED_VERIFICATION_SCHEMA
            or runtime_reference.get("main_artifact_sha256")
            != result_bundle["main_artifact_sha256"]
            or runtime_reference.get(
                "market_breadth_feature_binding_receipt_sha256"
            )
            != result_bundle[
                "market_breadth_feature_binding_receipt_sha256"
            ]
        ):
            raise ValueError("formal result physical chain drifted")
        formal_history = _formal_runtime_history(preflight_core, dict(postflight))
        portable_bundle = {
            key: value
            for key, value in result_bundle.items()
            if key
            in {
                "main_artifact_sha256",
                "main_document",
                "sidecar_artifact_sha256",
                "sidecar_documents",
                "runtime_verification",
                "market_breadth_filter_receipt_sha256",
                "market_breadth_feature_binding_receipt_sha256",
            }
        }
        return {
            "source_root": source_root,
            "run_root": run_root,
            "claim": claim,
            "preflight_document": preflight_document,
            "preflight_core": preflight_core,
            "launch": launch,
            "resource_receipt": resource,
            "completion": completion,
            "terminal": terminal,
            "completion_sha256": completion_sha256,
            "result_bundle": portable_bundle,
            "formal_runtime_history": formal_history,
            "source_authority": source_authority,
            "verifier_amendment": verifier_amendment,
            "formal_stdout": formal_stdout,
            "formal_stderr": formal_stderr,
            "file_sha256": {
                "claim": claim_sha256,
                "preflight": hashlib.sha256(preflight_raw).hexdigest(),
                "launch": hashlib.sha256(launch_raw).hexdigest(),
                "resource": hashlib.sha256(resource_raw).hexdigest(),
                "completion": completion_sha256,
                "terminal": hashlib.sha256(terminal_raw).hexdigest(),
                "stdout": formal_stdout["sha256"],
                "stderr": formal_stderr["sha256"],
            },
        }
    except IndependentVerificationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise IndependentVerificationError("formal completion chain is invalid") from exc


def _load_retry_inputs(source_root_value: str | Path) -> dict[str, Any]:
    inputs = _load_formal_inputs(source_root_value)
    source_root = Path(source_root_value).resolve(strict=True)
    run_root = Path(inputs["run_root"])
    original_failed_attempt = _verified_original_failed_attempt(run_root)
    retry_authority = _verified_post_failure_retry_authority(
        source_root,
        original_amendment=inputs["verifier_amendment"],
        original_failed_attempt=original_failed_attempt,
    )
    return {
        **inputs,
        "original_failed_attempt": original_failed_attempt,
        "retry_authority": retry_authority,
    }


def _assert_retry_slot_unused(run_root: Path) -> None:
    retry_paths = (
        RETRY_CLAIM_NAME,
        RETRY_STATUS_NAME,
        RETRY_VERIFICATION_ROOT_NAME,
        RETRY_RECEIPT_ROOT_NAME,
        RETRY_FAILURE_RECEIPT_ROOT_NAME,
        RETRY_REPLAY_STDOUT_NAME,
        RETRY_REPLAY_STDERR_NAME,
    )
    for relative in retry_paths:
        try:
            os.lstat(run_root / relative)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise IndependentVerificationError(
                "independent verification retry_1 slot is unreadable"
            ) from exc
        raise FileExistsError("independent verification retry_1 is already claimed")


RUNTIME_PROBE = r'''
import json
from pathlib import Path
import sys

code_root = str(Path(sys.argv[1]).resolve(strict=True))
site_packages = str(Path(sys.argv[2]).resolve(strict=True))
sys.path[:0] = [code_root, site_packages]
from app import audited_pit_continuous_ridge_oof as module

print(json.dumps({
    "ridge_binding": module._producer_binding(artifact_version=3),
    "shallow_binding": module._shallow_gbdt_producer_binding(),
    "risk_binding": module._shallow_gbdt_risk_on_breadth_producer_binding(),
}, ensure_ascii=False, sort_keys=True))
'''
CURRENT_POOL_AUDIT_PROBE = r'''
import json
from pathlib import Path
import sys

code_root = str(Path(sys.argv[1]).resolve(strict=True))
site_packages = str(Path(sys.argv[2]).resolve(strict=True))
audit_path = Path(sys.argv[3]).resolve(strict=True)
sys.path[:0] = [code_root, site_packages]
from app.current_pool_gate import verify_current_pool_audit

value = verify_current_pool_audit(audit_path)
allowed = value.get("allowed_symbols")
canonical = value.get("canonical_sha256")
if not isinstance(allowed, set) or not isinstance(canonical, str):
    raise SystemExit(66)
print(json.dumps({
    "canonical_sha256": canonical,
    "allowed_symbol_count": len(allowed),
}, ensure_ascii=False, sort_keys=True))
'''


def _probe_runtime(
    code_root: Path,
    *,
    python_executable: Path,
    site_packages: Path,
    environment: Mapping[str, str],
    pycache_blocker: Path,
) -> dict[str, Any]:
    command = [
        str(python_executable.resolve(strict=True)),
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={pycache_blocker.resolve(strict=True)}",
        "-c",
        RUNTIME_PROBE,
        str(code_root.resolve(strict=True)),
        str(site_packages.resolve(strict=True)),
    ]
    completed = subprocess.run(
        command,
        cwd=code_root,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("runtime producer probe failed")
    try:
        value = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise IndependentVerificationError("runtime producer probe is invalid") from exc
    if not isinstance(value, Mapping) or set(value) != {
        "ridge_binding",
        "shallow_binding",
        "risk_binding",
    }:
        raise IndependentVerificationError("runtime producer probe fields drifted")
    ridge, ridge_root = _verified_binding(
        value.get("ridge_binding"),
        schema_version="audited-pit-ranked-liquidity-producer/v3",
        label="runtime ridge binding",
        schema_in_root=False,
    )
    shallow, shallow_root, normalized_shallow = _normalized_shallow_binding(
        value.get("shallow_binding"),
        "runtime shallow binding",
    )
    if shallow.get("base_ranked_liquidity_producer_root_sha256") != ridge_root:
        raise IndependentVerificationError("runtime shallow producer drifted")
    risk, _risk_root, _normalized_risk = _normalized_risk_binding(
        value.get("risk_binding"),
        expected_shallow_root_sha256=shallow_root,
        normalized_shallow_root_sha256=normalized_shallow["root_sha256"],
        label="runtime risk binding",
    )
    return {
        "ridge_binding": ridge,
        "shallow_binding": shallow,
        "risk_binding": risk,
    }


def _probe_current_pool_audit(
    code_root: Path,
    *,
    audit_path: Path,
    expected_binding: Mapping[str, Any],
    python_executable: Path,
    site_packages: Path,
    environment: Mapping[str, str],
    pycache_blocker: Path,
) -> dict[str, Any]:
    expected = dict(expected_binding)
    if (
        set(expected) != {"canonical_sha256", "allowed_symbol_count"}
        or not HEX_SHA256.fullmatch(str(expected.get("canonical_sha256") or ""))
        or isinstance(expected.get("allowed_symbol_count"), bool)
        or not isinstance(expected.get("allowed_symbol_count"), int)
        or expected["allowed_symbol_count"] <= 0
    ):
        raise IndependentVerificationError("current-pool audit binding is invalid")
    command = [
        str(python_executable.resolve(strict=True)),
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={pycache_blocker.resolve(strict=True)}",
        "-c",
        CURRENT_POOL_AUDIT_PROBE,
        str(code_root.resolve(strict=True)),
        str(site_packages.resolve(strict=True)),
        str(audit_path.resolve(strict=True)),
    ]
    completed = subprocess.run(
        command,
        cwd=code_root,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("current-pool audit probe failed")
    try:
        observed = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise IndependentVerificationError("current-pool audit probe is invalid") from exc
    if not isinstance(observed, Mapping) or dict(observed) != expected:
        raise IndependentVerificationError("current-pool audit binding drifted")
    return expected


def _execution_snapshot(
    *,
    formal_probe: Mapping[str, Any],
    replay_probe: Mapping[str, Any],
    output_dir: Path,
    formal_runtime_history: Mapping[str, Any],
) -> dict[str, Any]:
    formal_shallow = formal_probe["shallow_binding"]
    replay_shallow = replay_probe["shallow_binding"]
    formal_library = Path(
        _xgboost_library_value(
            formal_shallow["xgboost_build_info"],
            "formal xgboost build info",
        )
    )
    replay_library = Path(
        _xgboost_library_value(
            replay_shallow["xgboost_build_info"],
            "replay xgboost build info",
        )
    )
    body = {
        "schema_version": EXECUTION_SNAPSHOT_SCHEMA,
        "entrypoint": {
            "module": "app.jobs",
            "dispatch": "runpy.run_module",
            "argv": _jobs_argv(str(output_dir.resolve())),
            "output_dir": str(output_dir.resolve()),
            "driver_sha256": _sha256(ISOLATED_REPLAY_DRIVER),
            "direct_callable_used": False,
        },
        "xgboost_relocation": {
            "formal_ridge_binding": formal_probe["ridge_binding"],
            "replay_ridge_binding": replay_probe["ridge_binding"],
            "formal_shallow_binding": formal_shallow,
            "replay_shallow_binding": replay_shallow,
            "formal_risk_binding": formal_probe["risk_binding"],
            "replay_risk_binding": replay_probe["risk_binding"],
            "formal_xgboost_library_sha256": _sha256_regular_file(
                formal_library,
                "formal xgboost library",
            ),
            "replay_xgboost_library_sha256": _sha256_regular_file(
                replay_library,
                "replay xgboost library",
            ),
            "formal_preflight_xgboost_distribution_files_sha256": (
                formal_runtime_history[
                    "preflight_xgboost_distribution_files_sha256"
                ]
            ),
            "formal_postflight_xgboost_distribution_files_sha256": (
                formal_runtime_history[
                    "postflight_xgboost_distribution_files_sha256"
                ]
            ),
        },
    }
    snapshot = {**body, "snapshot_sha256": _sha256(body)}
    _verified_execution_snapshot(
        snapshot,
        expected=snapshot,
        formal_runtime_history=formal_runtime_history,
    )
    return snapshot


def _verify_frozen_copy(
    source_root: Path,
    data_root: Path,
    expected: Mapping[str, Any],
    *,
    expected_formal_attestation: Mapping[str, Any],
) -> None:
    inputs = REPLAY_PLAN["inputs"]
    source_bundle = _universe_bundle_manifest(source_root)
    target_bundle = _universe_bundle_manifest(data_root)
    if source_bundle != target_bundle:
        raise IndependentVerificationError("frozen replay universe bundle drifted")
    observed: dict[str, dict[str, Any]] = {
        "audited_pit_universe_bundle_root": target_bundle,
    }
    for field in (
        "temporal_contract_path",
        "current_pool_development_audit_path",
        "security_code_transition_evidence_root",
    ):
        relative = Path(str(inputs[field]))
        source_manifest = _manifest_path(source_root / relative, field)
        target_manifest = _manifest_path(data_root / relative, field)
        if source_manifest != target_manifest:
            raise IndependentVerificationError("frozen replay input drifted")
        observed[field] = target_manifest
    _assert_sqlite_quiescent(
        source_root / Path(str(inputs["audited_pit_universe_path"]))
    )
    _assert_sqlite_quiescent(
        data_root / Path(str(inputs["audited_pit_universe_path"]))
    )
    formal_attestation = _observed_frozen_attestation(source_root)
    if formal_attestation != dict(expected_formal_attestation):
        raise IndependentVerificationError("formal frozen input history drifted")
    body = {
        "schema_version": "risk-on-breadth-independent-frozen-input-copy/v2",
        "input_count": 4,
        "manifests": observed,
        "universe_bundle_manifest_sha256": source_bundle["manifest_sha256"],
        "formal_frozen_input_attestation_root_sha256": (
            formal_attestation["root_sha256"]
        ),
    }
    if {**body, "root_sha256": _sha256(body)} != dict(expected):
        raise IndependentVerificationError("frozen replay input binding drifted")


def _load_replay_bundle(
    output_dir: Path,
    *,
    expected_producer_binding: Mapping[str, Any],
) -> dict[str, Any]:
    verification_dir = output_dir / "verifications"
    entries = _directory_entries(
        verification_dir,
        "replayed runtime verification directory",
    )
    if len(entries) != 1:
        raise IndependentVerificationError("replayed runtime verification is not unique")
    runtime = _read_json_object(
        entries[0],
        "replayed runtime verification",
        document_class="runtime_verification",
    )
    bundle = _load_content_addressed_result_bundle(
        output_dir,
        runtime_verification=runtime,
        expected_producer_binding=expected_producer_binding,
    )
    return {
        key: value
        for key, value in bundle.items()
        if key
        in {
            "main_artifact_sha256",
            "main_document",
            "sidecar_artifact_sha256",
            "sidecar_documents",
            "runtime_verification",
            "market_breadth_filter_receipt_sha256",
            "market_breadth_feature_binding_receipt_sha256",
        }
    }


def _run_isolated_replay(
    *,
    inputs: Mapping[str, Any],
    source_root: Path,
    code_root: Path,
    data_root: Path,
    python_executable: Path,
    site_packages: Path,
    environment: Mapping[str, str],
    pycache_blocker: Path,
    formal_probe: Mapping[str, Any],
    replay_probe: Mapping[str, Any],
    frozen_copy: Mapping[str, Any],
    scratch_root: Path,
    replay_stream_ownerships: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    output_dir = scratch_root / "replay-output"
    ownership_by_relative = {
        str(ownership.get("relative")): ownership
        for ownership in replay_stream_ownerships
    }
    if set(ownership_by_relative) != {
        RETRY_REPLAY_STDOUT_NAME,
        RETRY_REPLAY_STDERR_NAME,
    }:
        raise IndependentVerificationError("retry replay stream ownership is invalid")
    stdout_ownership = ownership_by_relative[RETRY_REPLAY_STDOUT_NAME]
    stderr_ownership = ownership_by_relative[RETRY_REPLAY_STDERR_NAME]
    stdout_handle = stdout_ownership["handle"]
    stderr_handle = stderr_ownership["handle"]
    command = _isolated_replay_command(
        python_executable=python_executable,
        code_root=code_root,
        site_packages=site_packages,
        output_dir=output_dir,
        pycache_blocker=pycache_blocker,
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=data_root,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        shell=False,
        creationflags=creationflags,
    )
    exit_code = process.wait()
    _seal_retry_replay_stream_ownership(stdout_ownership)
    _seal_retry_replay_stream_ownership(stderr_ownership)
    stdout = stdout_ownership["descriptor"]
    stderr = stderr_ownership["descriptor"]
    postflight_error: BaseException | None = None
    try:
        _verify_frozen_copy(
            source_root,
            data_root,
            frozen_copy,
            expected_formal_attestation=inputs["preflight_core"][
                "frozen_input_attestation"
            ],
        )
        _probe_current_pool_audit(
            code_root,
            audit_path=(
                data_root
                / Path(
                    str(
                        REPLAY_PLAN["inputs"][
                            "current_pool_development_audit_path"
                        ]
                    )
                )
            ),
            expected_binding=inputs["preflight_core"][
                "current_pool_audit_binding"
            ],
            python_executable=python_executable,
            site_packages=site_packages,
            environment=environment,
            pycache_blocker=pycache_blocker,
        )
        replay_post_probe = _probe_runtime(
            code_root,
            python_executable=python_executable,
            site_packages=site_packages,
            environment=environment,
            pycache_blocker=pycache_blocker,
        )
        if replay_post_probe != dict(replay_probe):
            raise IndependentVerificationError(
                "replay runtime changed during execution"
            )
    except BaseException as exc:
        postflight_error = exc
    if exit_code != 0:
        raise IndependentReplayProcessError(exit_code) from postflight_error
    if postflight_error is not None:
        raise postflight_error
    execution_snapshot = _execution_snapshot(
        formal_probe=formal_probe,
        replay_probe=replay_probe,
        output_dir=output_dir,
        formal_runtime_history=inputs["formal_runtime_history"],
    )
    replay_bundle = _load_replay_bundle(
        output_dir,
        expected_producer_binding=replay_probe["risk_binding"],
    )
    replay = {
        "schema_version": REPLAY_RESULT_SCHEMA,
        "execution_snapshot": execution_snapshot,
        "result_bundle": replay_bundle,
    }
    return replay, execution_snapshot, stdout, stderr


def _seal_retry_replay_stream_ownership(
    ownership: dict[str, Any],
) -> dict[str, Any]:
    if ownership.get("descriptor") is not None:
        _verify_retry_replay_stream_ownership(ownership)
        return ownership
    path = ownership["path"]
    handle = ownership["handle"]
    label = str(ownership["label"])
    handle.flush()
    locked = _lock_control_file(handle, label)
    metadata_before = _assert_no_reparse(path, label)
    opened_before = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(opened_before.st_mode)
        or _stable_object_identity(opened_before)
        != ownership["object_identity"]
        or _stable_object_identity(metadata_before)
        != ownership["object_identity"]
    ):
        raise IndependentVerificationError(f"{label} changed before sealing")
    digest = hashlib.sha256()
    size = 0
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    opened_after = os.fstat(handle.fileno())
    metadata_after = _assert_no_reparse(path, label)
    identity = _owned_file_identity(opened_before)
    if (
        _owned_file_identity(opened_after) != identity
        or _owned_file_identity(metadata_after) != identity
        or size != opened_before.st_size
    ):
        raise IndependentVerificationError(f"{label} changed while sealing")
    ownership["identity"] = identity
    ownership["locked"] = locked
    ownership["descriptor"] = {
        "path": ownership["relative"],
        "bytes": size,
        "sha256": digest.hexdigest(),
    }
    return ownership


def _create_retry_replay_stream_ownerships(
    run_root: Path,
) -> tuple[dict[str, Any], ...]:
    ownerships: list[dict[str, Any]] = []
    created_paths: list[Path] = []
    try:
        for name, relative in (
            ("stdout", RETRY_REPLAY_STDOUT_NAME),
            ("stderr", RETRY_REPLAY_STDERR_NAME),
        ):
            path = run_root / relative
            handle = _open_control_file(
                path,
                create=True,
                writable=True,
            )
            created_paths.append(path)
            opened = os.fstat(handle.fileno())
            ownerships.append(
                {
                    "path": path,
                    "relative": relative,
                    "handle": handle,
                    "object_identity": _stable_object_identity(opened),
                    "parent_identity": _stable_object_identity(
                        _assert_no_reparse(
                            path.parent,
                            f"retry replay {name} parent",
                        )
                    ),
                    "identity": None,
                    "descriptor": None,
                    "locked": False,
                    "label": f"retry replay {name}",
                }
            )
        return tuple(ownerships)
    except BaseException:
        _close_retry_replay_stream_ownerships(tuple(ownerships))
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


def _retry_replay_stream_ownership(
    path: Path,
    *,
    relative: str,
    label: str,
) -> dict[str, Any]:
    metadata_before = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata_before.st_mode):
        raise IndependentVerificationError(f"{label} is invalid")
    handle = None
    try:
        handle = _open_control_file(
            path,
            create=False,
            writable=False,
        )
        opened = os.fstat(handle.fileno())
        if _owned_file_identity(opened) != _owned_file_identity(metadata_before):
            raise IndependentVerificationError(f"{label} changed before open")
        ownership = {
            "path": path,
            "relative": relative,
            "handle": handle,
            "object_identity": _stable_object_identity(opened),
            "parent_identity": _stable_object_identity(
                _assert_no_reparse(path.parent, f"{label} parent")
            ),
            "identity": None,
            "descriptor": None,
            "locked": False,
            "label": label,
        }
        return _seal_retry_replay_stream_ownership(ownership)
    except BaseException:
        if handle is not None:
            handle.close()
        raise


def _verify_retry_replay_stream_ownership(
    ownership: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        path = ownership["path"]
        handle = ownership["handle"]
        identity = ownership["identity"]
        expected = ownership["descriptor"]
        label = str(ownership["label"])
        metadata_before = _assert_no_reparse(path, label)
        opened_before = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        size = 0
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
        opened_after = os.fstat(handle.fileno())
        metadata_after = _assert_no_reparse(path, label)
        parent_after = _assert_no_reparse(path.parent, f"{label} parent")
        observed = {
            "path": expected["path"],
            "bytes": size,
            "sha256": digest.hexdigest(),
        }
        if (
            _owned_file_identity(metadata_before) != identity
            or _owned_file_identity(opened_before) != identity
            or _owned_file_identity(opened_after) != identity
            or _owned_file_identity(metadata_after) != identity
            or _stable_object_identity(parent_after)
            != ownership["parent_identity"]
            or observed != expected
        ):
            raise IndependentVerificationError(f"{label} ownership changed")
        return observed
    except IndependentVerificationError:
        raise
    except BaseException as exc:
        raise IndependentVerificationError(
            "retry replay stream ownership is unavailable"
        ) from exc


def _owned_retry_replay_streams(
    run_root: Path,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    result: dict[str, Any] = {}
    ownerships: list[dict[str, Any]] = []
    try:
        for name, relative in (
            ("stdout", RETRY_REPLAY_STDOUT_NAME),
            ("stderr", RETRY_REPLAY_STDERR_NAME),
        ):
            path = run_root / relative
            try:
                os.lstat(path)
            except FileNotFoundError:
                result[name] = None
                continue
            except OSError as exc:
                raise IndependentVerificationError(
                    f"retry replay {name} is unavailable"
                ) from exc
            ownership = _retry_replay_stream_ownership(
                path,
                relative=relative,
                label=f"retry replay {name}",
            )
            ownerships.append(ownership)
            result[name] = ownership["descriptor"]
        return result, tuple(ownerships)
    except BaseException:
        _close_retry_replay_stream_ownerships(tuple(ownerships))
        raise


def _verify_retry_replay_stream_ownerships(
    ownerships: tuple[Mapping[str, Any], ...],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    observed = {"stdout": None, "stderr": None}
    for ownership in ownerships:
        descriptor = _verify_retry_replay_stream_ownership(ownership)
        name = "stdout" if descriptor["path"] == RETRY_REPLAY_STDOUT_NAME else "stderr"
        observed[name] = descriptor
    if observed != dict(expected):
        raise IndependentVerificationError(
            "retry replay stream descriptors changed"
        )
    return observed


def _close_retry_replay_stream_ownerships(
    ownerships: tuple[Mapping[str, Any], ...],
) -> None:
    for ownership in ownerships:
        handle = ownership.get("handle")
        if handle is not None and not handle.closed:
            handle.close()


def _persisted_retry_replay_streams(run_root: Path) -> dict[str, Any]:
    streams, ownerships = _owned_retry_replay_streams(run_root)
    try:
        return streams
    finally:
        _close_retry_replay_stream_ownerships(ownerships)


def _verified_retry_replay_streams(
    run_root: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    observed = _persisted_retry_replay_streams(run_root)
    if observed != dict(expected):
        raise IndependentVerificationError(
            "retry replay stream descriptors changed"
        )
    return observed


def _content_addressed_payload(body: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    artifact_sha256 = _sha256(body)
    return {**dict(body), "artifact_sha256": artifact_sha256}, artifact_sha256


def _formal_chain_fingerprint(inputs: Mapping[str, Any]) -> str:
    amendment = inputs.get("verifier_amendment")
    amendment_value = dict(amendment) if isinstance(amendment, Mapping) else {}
    return _sha256(
        {
            "claim": inputs.get("claim"),
            "preflight_document": inputs.get("preflight_document"),
            "preflight_core": inputs.get("preflight_core"),
            "launch": inputs.get("launch"),
            "resource_receipt": inputs.get("resource_receipt"),
            "completion": inputs.get("completion"),
            "terminal": inputs.get("terminal"),
            "completion_sha256": inputs.get("completion_sha256"),
            "result_bundle": inputs.get("result_bundle"),
            "formal_runtime_history": inputs.get("formal_runtime_history"),
            "source_authority": inputs.get("source_authority"),
            "verifier_amendment": amendment,
            "verifier_amendment_sha256": amendment_value.get(
                "artifact_sha256"
            ),
            "successor_verifier_git_blob_sha256": amendment_value.get(
                "successor_verifier_git_blob_sha256"
            ),
            "json_document_size_policy_sha256": amendment_value.get(
                "json_document_size_policy_sha256"
            ),
            "formal_stdout": inputs.get("formal_stdout"),
            "formal_stderr": inputs.get("formal_stderr"),
            "file_sha256": inputs.get("file_sha256"),
            "original_failed_attempt": inputs.get("original_failed_attempt"),
            "retry_authority": inputs.get("retry_authority"),
        }
    )


def _assert_formal_chain_unchanged(
    source_root: Path,
    expected_fingerprint: str,
) -> None:
    observed = _formal_chain_fingerprint(_load_retry_inputs(source_root))
    if observed != expected_fingerprint:
        raise IndependentVerificationError("formal completion chain changed during replay")


def _publish_retry_failure(
    *,
    run_root: Path,
    status_ownership: dict[str, Any] | None,
    claim_ownership: Mapping[str, Any],
    stage: str,
    error: BaseException,
    completion_sha256: object,
    verifier_amendment: Mapping[str, Any],
    retry_authority: Mapping[str, Any],
    original_failed_attempt: Mapping[str, Any],
    existing_replay_stream_ownerships: (
        tuple[dict[str, Any], ...] | None
    ) = None,
) -> None:
    _verify_claim_ownership(claim_ownership)
    descriptor_error_type: str | None = None
    replay_stream_ownerships: tuple[dict[str, Any], ...] = ()
    try:
        if existing_replay_stream_ownerships is None:
            replay_streams, replay_stream_ownerships = (
                _owned_retry_replay_streams(run_root)
            )
        else:
            replay_stream_ownerships = existing_replay_stream_ownerships
            replay_streams = {"stdout": None, "stderr": None}
            for ownership in replay_stream_ownerships:
                _seal_retry_replay_stream_ownership(ownership)
                descriptor = _verify_retry_replay_stream_ownership(ownership)
                name = (
                    "stdout"
                    if descriptor["path"] == RETRY_REPLAY_STDOUT_NAME
                    else "stderr"
                )
                replay_streams[name] = descriptor
    except BaseException as descriptor_error:
        descriptor_error_type = type(descriptor_error).__name__
        replay_streams = {"stdout": None, "stderr": None}
        invalid_ownerships = replay_stream_ownerships
        replay_stream_ownerships = ()
        try:
            _close_retry_replay_stream_ownerships(invalid_ownerships)
        except BaseException:
            pass
    child_exit_code = getattr(error, "exit_code", None)
    failure_body = {
        "schema_version": RETRY_FAILURE_RECEIPT_SCHEMA,
        "retry_id": "retry_1",
        "stage": stage,
        "error_type": type(error).__name__,
        "child_exit_code": child_exit_code,
        "descriptor_error_type": descriptor_error_type,
        "independent_replay": replay_streams,
        "claim_sha256": claim_ownership["sha256"],
        "completion_sha256": completion_sha256,
        "retry_authority_sha256": retry_authority["artifact_sha256"],
        "original_failed_claim_sha256": original_failed_attempt[
            "claim_sha256"
        ],
        "original_failed_status_sha256": original_failed_attempt[
            "status_sha256"
        ],
        "verified": False,
        "point_in_time": True,
        "development_only": True,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "production_authority": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }
    failure_document, candidate_failure_sha256 = _content_addressed_payload(
        failure_body
    )
    candidate_failure_path = (
        run_root
        / RETRY_FAILURE_RECEIPT_ROOT_NAME
        / f"{candidate_failure_sha256}.json"
    )
    failure_sha256: str | None = candidate_failure_sha256
    failure_path: str | None = (
        f"{RETRY_FAILURE_RECEIPT_ROOT_NAME}/{candidate_failure_sha256}.json"
    )
    publication_error_type: str | None = None
    failure_ownership: dict[str, Any] | None = None
    try:
        failure_ownership = _write_once(
            candidate_failure_path,
            _json_raw(failure_document),
            "independent verification retry_1 failure receipt",
            hold_ownership=True,
        )
        if failure_ownership is None:
            raise IndependentVerificationError(
                "failure receipt ownership was not retained"
            )
    except BaseException as publication_error:
        _close_publication_ownership(failure_ownership)
        failure_ownership = None
        failure_sha256 = None
        failure_path = None
        publication_error_type = type(publication_error).__name__
    _verify_claim_ownership(claim_ownership)
    if status_ownership is None:
        _close_publication_ownership(failure_ownership)
        _close_retry_replay_stream_ownerships(replay_stream_ownerships)
        raise IndependentVerificationError(
            "independent verification retry_1 status slot is not owned"
        )
    failed = {
        **_status_payload(
            verified=False,
            receipt_sha256=None,
            error_type=type(error).__name__,
        ),
        "schema_version": RETRY_STATUS_SCHEMA,
        "stage": stage,
        "retry_id": "retry_1",
        "claim_sha256": claim_ownership["sha256"],
        "completion_sha256": completion_sha256,
        "verifier_amendment_sha256": verifier_amendment[
            "artifact_sha256"
        ],
        "successor_verifier_git_blob_sha256": verifier_amendment[
            "successor_verifier_git_blob_sha256"
        ],
        "json_document_size_policy_sha256": verifier_amendment[
            "json_document_size_policy_sha256"
        ],
        "retry_authority_sha256": retry_authority["artifact_sha256"],
        "original_failed_claim_sha256": original_failed_attempt[
            "claim_sha256"
        ],
        "original_failed_status_sha256": original_failed_attempt[
            "status_sha256"
        ],
        "failure_receipt_sha256": failure_sha256,
        "failure_receipt_path": failure_path,
        "descriptor_error_type": descriptor_error_type,
        "publication_error_type": publication_error_type,
        "independent_replay": {
            "exit_code": child_exit_code,
            **replay_streams,
        },
        "receipt_path": None,
    }
    try:
        _finalize_status_slot(
            status_ownership,
            _json_raw(failed),
            claim_ownership=claim_ownership,
            publication_ownerships=(
                () if failure_ownership is None else (failure_ownership,)
            ),
            replay_stream_ownerships=replay_stream_ownerships,
        )
    finally:
        _close_publication_ownership(failure_ownership)
        _close_retry_replay_stream_ownerships(replay_stream_ownerships)


def run(source_root_value: str) -> dict[str, str]:
    inputs = _load_retry_inputs(source_root_value)
    formal_chain_fingerprint = _formal_chain_fingerprint(inputs)
    run_root = Path(inputs["run_root"])
    _assert_retry_slot_unused(run_root)
    claim_path = run_root / RETRY_CLAIM_NAME
    status_path = run_root / RETRY_STATUS_NAME
    source_root = Path(source_root_value).resolve(strict=True)
    verifier_amendment = inputs["verifier_amendment"]
    retry_authority = inputs["retry_authority"]
    original_failed_attempt = inputs["original_failed_attempt"]
    claim = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
            "independent-verification-retry-claim/v1"
        ),
        "retry_id": "retry_1",
        "pid": os.getpid(),
        "completion_sha256": inputs.get("completion_sha256"),
        "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
        "verifier_amendment_sha256": verifier_amendment["artifact_sha256"],
        "successor_verifier_git_blob_sha256": verifier_amendment[
            "successor_verifier_git_blob_sha256"
        ],
        "json_document_size_policy_sha256": verifier_amendment[
            "json_document_size_policy_sha256"
        ],
        "retry_authority_sha256": retry_authority["artifact_sha256"],
        "retry_verifier_git_blob_sha256": retry_authority[
            "retry_verifier_git_blob_sha256"
        ],
        "original_failed_claim_sha256": original_failed_attempt[
            "claim_sha256"
        ],
        "original_failed_status_sha256": original_failed_attempt[
            "status_sha256"
        ],
        "universe_bundle_manifest_sha256": retry_authority[
            "universe_bundle_manifest_sha256"
        ],
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    claim_raw = _json_raw(claim)
    claim_ownership = _write_once(
        claim_path,
        claim_raw,
        "independent verification retry_1 claim",
        hold_ownership=True,
    )
    if claim_ownership is None:
        raise IndependentVerificationError("retry claim ownership was not retained")
    status_ownership: dict[str, Any] | None = None
    verification_ownership: dict[str, Any] | None = None
    receipt_ownership: dict[str, Any] | None = None
    replay_stream_ownerships: tuple[dict[str, Any], ...] = ()
    stage = "status_reservation"
    try:
        status_ownership = _reserve_status_slot(status_path)
        stage = "runtime_preparation"
        python_executable = _assert_project_interpreter(source_root)
        site_packages = (source_root / ".venv/Lib/site-packages").resolve(
            strict=True
        )
        with tempfile.TemporaryDirectory(prefix="risk-breadth-independent-") as temporary:
            temporary_root = Path(temporary)
            formal_blocker = (source_root / PYCACHE_BLOCKER_RELATIVE).resolve(
                strict=True
            )
            if _file_sha256(formal_blocker) != EXPECTED_PYCACHE_BLOCKER_SHA256:
                raise IndependentVerificationError("formal pycache blocker drifted")
            environment = _minimal_child_environment(
                temporary_root / "runtime-tmp",
                python_executable=python_executable,
                pycache_blocker=formal_blocker,
            )
            formal_probe = _probe_runtime(
                source_root,
                python_executable=python_executable,
                site_packages=site_packages,
                environment=environment,
                pycache_blocker=formal_blocker,
            )
            current_pool_binding = inputs["preflight_core"][
                "current_pool_audit_binding"
            ]
            current_pool_relative = Path(
                str(REPLAY_PLAN["inputs"]["current_pool_development_audit_path"])
            )
            scratch = temporary_root / "snapshot"
            with _detached_source_snapshot(
                source_root,
                inputs["source_authority"],
                scratch,
            ) as code_root:
                blocker = (code_root / PYCACHE_BLOCKER_RELATIVE).resolve(strict=True)
                if _file_sha256(blocker) != EXPECTED_PYCACHE_BLOCKER_SHA256:
                    raise IndependentVerificationError("snapshot pycache blocker drifted")
                data_root = temporary_root / "execution-root"
                _probe_current_pool_audit(
                    code_root,
                    audit_path=source_root / current_pool_relative,
                    expected_binding=current_pool_binding,
                    python_executable=python_executable,
                    site_packages=site_packages,
                    environment=environment,
                    pycache_blocker=blocker,
                )
                stage = "frozen_input_copy"
                frozen_copy = _copy_frozen_inputs(
                    source_root,
                    data_root,
                    expected_formal_attestation=inputs["preflight_core"][
                        "frozen_input_attestation"
                    ],
                    expected_universe_bundle_manifest_sha256=retry_authority[
                        "universe_bundle_manifest_sha256"
                    ],
                )
                replay_probe = _probe_runtime(
                    code_root,
                    python_executable=python_executable,
                    site_packages=site_packages,
                    environment=environment,
                    pycache_blocker=blocker,
                )
                _probe_current_pool_audit(
                    code_root,
                    audit_path=data_root / current_pool_relative,
                    expected_binding=current_pool_binding,
                    python_executable=python_executable,
                    site_packages=site_packages,
                    environment=environment,
                    pycache_blocker=blocker,
                )
                stage = "replay_execution"
                replay_stream_ownerships = (
                    _create_retry_replay_stream_ownerships(run_root)
                )
                replay, snapshot, stdout, stderr = _run_isolated_replay(
                    inputs=inputs,
                    source_root=source_root,
                    code_root=code_root,
                    data_root=data_root,
                    python_executable=python_executable,
                    site_packages=site_packages,
                    environment=environment,
                    pycache_blocker=blocker,
                    formal_probe=formal_probe,
                    replay_probe=replay_probe,
                    frozen_copy=frozen_copy,
                    scratch_root=temporary_root,
                    replay_stream_ownerships=replay_stream_ownerships,
                )
                stage = "replay_validation"
                verification = _validate_replay(inputs, replay, snapshot)
                _verify_frozen_copy(
                    source_root,
                    data_root,
                    frozen_copy,
                    expected_formal_attestation=inputs["preflight_core"][
                        "frozen_input_attestation"
                    ],
                )
            _assert_formal_chain_unchanged(
                source_root,
                formal_chain_fingerprint,
            )
            _verify_claim_ownership(claim_ownership)
            expected_replay_streams = {
                "stdout": stdout,
                "stderr": stderr,
            }
            _verify_retry_replay_stream_ownerships(
                replay_stream_ownerships,
                expected_replay_streams,
            )
            verification_document, verification_sha256 = _content_addressed_payload(
                verification
            )
            stage = "verification_publication"
            verification_path = (
                run_root
                / RETRY_VERIFICATION_ROOT_NAME
                / f"{verification_sha256}.json"
            )
            verification_raw = _json_raw(verification_document)
            verification_ownership = _write_once(
                verification_path,
                verification_raw,
                "independent verification artifact",
                hold_ownership=True,
            )
            if verification_ownership is None:
                raise IndependentVerificationError(
                    "verification artifact ownership was not retained"
                )
            _assert_formal_chain_unchanged(
                source_root,
                formal_chain_fingerprint,
            )
            _verify_claim_ownership(claim_ownership)
            _verify_retry_replay_stream_ownerships(
                replay_stream_ownerships,
                expected_replay_streams,
            )
            receipt_body = {
                **_receipt_body(verification_sha256=verification_sha256),
                "schema_version": RETRY_RECEIPT_SCHEMA,
                "retry_id": "retry_1",
                "completion_sha256": inputs["completion_sha256"],
                "verifier_amendment_sha256": verifier_amendment[
                    "artifact_sha256"
                ],
                "successor_verifier_git_blob_sha256": verifier_amendment[
                    "successor_verifier_git_blob_sha256"
                ],
                "json_document_size_policy_sha256": verifier_amendment[
                    "json_document_size_policy_sha256"
                ],
                "retry_authority_sha256": retry_authority[
                    "artifact_sha256"
                ],
                "original_failed_claim_sha256": original_failed_attempt[
                    "claim_sha256"
                ],
                "original_failed_status_sha256": original_failed_attempt[
                    "status_sha256"
                ],
                "universe_bundle_manifest_sha256": retry_authority[
                    "universe_bundle_manifest_sha256"
                ],
                "execution_snapshot_sha256": snapshot["snapshot_sha256"],
                "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
                "frozen_input_copy_root_sha256": frozen_copy["root_sha256"],
                "independent_replay": {
                    "exit_code": 0,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            }
            receipt_document, receipt_sha256 = _content_addressed_payload(
                receipt_body
            )
            stage = "receipt_publication"
            receipt_path = run_root / RETRY_RECEIPT_ROOT_NAME / f"{receipt_sha256}.json"
            receipt_raw = _json_raw(receipt_document)
            receipt_ownership = _write_once(
                receipt_path,
                receipt_raw,
                "independent verification receipt",
                hold_ownership=True,
            )
            if receipt_ownership is None:
                raise IndependentVerificationError(
                    "verification receipt ownership was not retained"
                )
            _verify_claim_ownership(claim_ownership)
        _assert_formal_chain_unchanged(
            source_root,
            formal_chain_fingerprint,
        )
        _verify_claim_ownership(claim_ownership)
        final_replay_streams = _verify_retry_replay_stream_ownerships(
            replay_stream_ownerships,
            expected_replay_streams,
        )
        status = {
            **_status_payload(
                verified=True,
                receipt_sha256=receipt_sha256,
                error_type=None,
            ),
            "schema_version": RETRY_STATUS_SCHEMA,
            "stage": "completed",
            "retry_id": "retry_1",
            "claim_sha256": claim_ownership["sha256"],
            "completion_sha256": inputs["completion_sha256"],
            "verifier_amendment_sha256": verifier_amendment[
                "artifact_sha256"
            ],
            "successor_verifier_git_blob_sha256": verifier_amendment[
                "successor_verifier_git_blob_sha256"
            ],
            "json_document_size_policy_sha256": verifier_amendment[
                "json_document_size_policy_sha256"
            ],
            "retry_authority_sha256": retry_authority["artifact_sha256"],
            "original_failed_claim_sha256": original_failed_attempt[
                "claim_sha256"
            ],
            "original_failed_status_sha256": original_failed_attempt[
                "status_sha256"
            ],
            "failure_receipt_sha256": None,
            "failure_receipt_path": None,
            "independent_replay": {
                "exit_code": 0,
                **final_replay_streams,
            },
            "receipt_path": (
                f"{RETRY_RECEIPT_ROOT_NAME}/{receipt_sha256}.json"
            ),
        }
        _finalize_status_slot(
            status_ownership,
            _json_raw(status),
            claim_ownership=claim_ownership,
            publication_ownerships=(
                verification_ownership,
                receipt_ownership,
            ),
            replay_stream_ownerships=replay_stream_ownerships,
        )
        return {"status": "completed", "receipt_sha256": receipt_sha256}
    except BaseException as exc:
        try:
            _publish_retry_failure(
                run_root=run_root,
                status_ownership=status_ownership,
                claim_ownership=claim_ownership,
                stage=stage,
                error=exc,
                completion_sha256=inputs.get("completion_sha256"),
                verifier_amendment=verifier_amendment,
                retry_authority=retry_authority,
                original_failed_attempt=original_failed_attempt,
                existing_replay_stream_ownerships=(
                    replay_stream_ownerships or None
                ),
            )
        except BaseException as publication_error:
            raise publication_error from exc
        raise
    finally:
        _close_retry_replay_stream_ownerships(replay_stream_ownerships)
        _close_publication_ownership(receipt_ownership)
        _close_publication_ownership(verification_ownership)
        _close_publication_ownership(status_ownership)
        _close_publication_ownership(claim_ownership)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        default=r"E:\AI workspace\quant-signal-lkj",
    )
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.preflight:
            inputs = _load_retry_inputs(args.source_root)
            _assert_retry_slot_unused(Path(inputs["run_root"]))
            print("status=preflight_verified")
            return 0
        run(args.source_root)
        print("status=independently_verified")
        return 0
    except Exception:
        print("status=failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
