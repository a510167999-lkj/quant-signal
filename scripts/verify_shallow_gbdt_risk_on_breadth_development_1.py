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
MAX_JSON_BYTES = 64 * 1024 * 1024
RECEIPT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-receipt/v1"
)
STATUS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "independent-verification-status/v1"
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


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    metadata = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON_BYTES:
        raise ValueError(f"{label} is not a bounded regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value


def _content_addressed_document(path: Path, label: str) -> dict[str, Any]:
    document = _read_json_object(path, label)
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once(path: Path, raw: bytes, label: str) -> None:
    if not isinstance(raw, bytes):
        raise TypeError(f"{label} bytes are invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{os.urandom(12).hex()}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            written = handle.write(raw)
            if written != len(raw):
                raise OSError(f"{label} short write")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_raw(value: Mapping[str, Any]) -> bytes:
    return _canonical_bytes(dict(value)) + b"\n"


def _read_launcher_json(
    path: Path,
    label: str,
    *,
    fields: frozenset[str] | None = None,
) -> tuple[dict[str, Any], bytes]:
    metadata = _assert_no_reparse(path, label)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON_BYTES:
        raise ValueError(f"{label} is invalid")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
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


def _copy_frozen_inputs(
    source_root: Path,
    data_root: Path,
    *,
    expected_formal_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = REPLAY_PLAN["inputs"]
    formal_attestation = _observed_frozen_attestation(source_root)
    if formal_attestation != dict(expected_formal_attestation):
        raise ValueError("formal frozen input changed before copy")
    plan = (
        ("audited_pit_universe_path", "file"),
        ("temporal_contract_path", "file"),
        ("current_pool_development_audit_path", "file"),
        ("security_code_transition_evidence_root", "directory_tree"),
    )
    source_before: dict[str, dict[str, Any]] = {}
    target_manifests: dict[str, dict[str, Any]] = {}
    for field, kind in plan:
        relative = Path(str(inputs[field]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("frozen input path is invalid")
        source = source_root / relative
        target = data_root / relative
        if field == "audited_pit_universe_path":
            _assert_sqlite_quiescent(source)
        source_before[field] = _manifest_path(source, f"frozen input {field}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("frozen input target already exists")
        if kind == "file":
            shutil.copyfile(source, target)
        else:
            shutil.copytree(source, target)
        if field == "audited_pit_universe_path":
            _assert_sqlite_quiescent(target)
        target_manifests[field] = _manifest_path(
            target,
            f"copied frozen input {field}",
        )
        if target_manifests[field] != source_before[field]:
            raise ValueError("frozen input copy differs")
    source_after = {
        field: _manifest_path(source_root / Path(str(inputs[field])), field)
        for field, _kind in plan
    }
    _assert_sqlite_quiescent(
        source_root / Path(str(inputs["audited_pit_universe_path"]))
    )
    if source_after != source_before:
        raise ValueError("frozen input changed during copy")
    if _observed_frozen_attestation(source_root) != formal_attestation:
        raise ValueError("formal frozen input changed during copy")
    body = {
        "schema_version": "risk-on-breadth-independent-frozen-input-copy/v1",
        "input_count": 4,
        "manifests": target_manifests,
        "formal_frozen_input_attestation_root_sha256": formal_attestation[
            "root_sha256"
        ],
    }
    return {**body, "root_sha256": _sha256(body)}


def _minimal_child_environment(temp_dir: Path) -> dict[str, str]:
    resolved_temp = temp_dir.resolve()
    resolved_temp.mkdir(parents=True, exist_ok=True)
    environment = {
        key: os.environ[key]
        for key in ("COMSPEC", "SYSTEMROOT", "SystemRoot", "WINDIR")
        if os.environ.get(key)
    }
    environment.update(
        {
            "DISABLE_ENV_FILE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "VPS_RUNTIME_ROLE": "local_research",
            "TEMP": str(resolved_temp),
            "TMP": str(resolved_temp),
        }
    )
    return environment


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
        document, _raw = _read_launcher_json(entries[0], "source authority")
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
        execution_commit = _git_output(source_root, "rev-parse", "HEAD").lower()
        relative_path = entries[0].relative_to(source_root).as_posix()
        if (
            set(document) != authority_fields
            or artifact_sha256 != _sha256(unsigned)
            or entries[0].stem != artifact_sha256
            or document.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-risk-on-breadth-source-authority/v1"
            or not HEX_GIT_SHA1.fullmatch(source_commit)
            or not HEX_GIT_SHA1.fullmatch(source_tree)
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
            or _git_output(source_root, "status", "--porcelain", "--untracked-files=all")
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
        if (
            hashlib.sha256(launcher_blob).hexdigest()
            != document.get("launcher_git_blob_sha256")
            or hashlib.sha256(verifier_blob).hexdigest()
            != document.get("verifier_git_blob_sha256")
            or verifier_blob != SCRIPT_PATH.read_bytes().replace(b"\r\n", b"\n")
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
    fields = (
        "audited_pit_universe_path",
        "temporal_contract_path",
        "current_pool_development_audit_path",
        "security_code_transition_evidence_root",
    )
    observed: dict[str, dict[str, Any]] = {}
    for field in fields:
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
        "schema_version": "risk-on-breadth-independent-frozen-input-copy/v1",
        "input_count": 4,
        "manifests": observed,
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
    runtime = _read_json_object(entries[0], "replayed runtime verification")
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
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    output_dir = scratch_root / "replay-output"
    stdout_path = scratch_root / REPLAY_STDOUT_NAME
    stderr_path = scratch_root / REPLAY_STDERR_NAME
    command = _isolated_replay_command(
        python_executable=python_executable,
        code_root=code_root,
        site_packages=site_packages,
        output_dir=output_dir,
        pycache_blocker=pycache_blocker,
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
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
        expected_binding=inputs["preflight_core"]["current_pool_audit_binding"],
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
        raise IndependentVerificationError("replay runtime changed during execution")
    if exit_code != 0:
        raise IndependentVerificationError("independent replay returned nonzero")
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
    stdout = {
        "path": REPLAY_STDOUT_NAME,
        "bytes": stdout_path.stat().st_size,
        "sha256": _file_sha256(stdout_path),
    }
    stderr = {
        "path": REPLAY_STDERR_NAME,
        "bytes": stderr_path.stat().st_size,
        "sha256": _file_sha256(stderr_path),
    }
    return replay, execution_snapshot, stdout, stderr


def _content_addressed_payload(body: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    artifact_sha256 = _sha256(body)
    return {**dict(body), "artifact_sha256": artifact_sha256}, artifact_sha256


def _formal_chain_fingerprint(inputs: Mapping[str, Any]) -> str:
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
            "formal_stdout": inputs.get("formal_stdout"),
            "formal_stderr": inputs.get("formal_stderr"),
            "file_sha256": inputs.get("file_sha256"),
        }
    )


def _assert_formal_chain_unchanged(
    source_root: Path,
    expected_fingerprint: str,
) -> None:
    observed = _formal_chain_fingerprint(_load_formal_inputs(source_root))
    if observed != expected_fingerprint:
        raise IndependentVerificationError("formal completion chain changed during replay")


def run(source_root_value: str) -> dict[str, str]:
    inputs = _load_formal_inputs(source_root_value)
    formal_chain_fingerprint = _formal_chain_fingerprint(inputs)
    run_root = Path(inputs["run_root"])
    claim_path = run_root / CLAIM_NAME
    status_path = run_root / STATUS_NAME
    if claim_path.exists() or status_path.exists():
        raise FileExistsError("independent verification is already claimed")
    source_root = Path(source_root_value).resolve(strict=True)
    python_executable = _assert_project_interpreter(source_root)
    claim = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
            "independent-verification-claim/v1"
        ),
        "pid": os.getpid(),
        "completion_sha256": inputs.get("completion_sha256"),
        "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    claim_raw = _json_raw(claim)
    _write_once(claim_path, claim_raw, "independent verification claim")
    try:
        site_packages = (source_root / ".venv/Lib/site-packages").resolve(
            strict=True
        )
        with tempfile.TemporaryDirectory(prefix="risk-breadth-independent-") as temporary:
            temporary_root = Path(temporary)
            environment = _minimal_child_environment(temporary_root / "runtime-tmp")
            formal_blocker = (source_root / PYCACHE_BLOCKER_RELATIVE).resolve(
                strict=True
            )
            if _file_sha256(formal_blocker) != EXPECTED_PYCACHE_BLOCKER_SHA256:
                raise IndependentVerificationError("formal pycache blocker drifted")
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
                frozen_copy = _copy_frozen_inputs(
                    source_root,
                    data_root,
                    expected_formal_attestation=inputs["preflight_core"][
                        "frozen_input_attestation"
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
                )
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
            verification_document, verification_sha256 = _content_addressed_payload(
                verification
            )
            verification_path = (
                run_root
                / VERIFICATION_ROOT_NAME
                / f"{verification_sha256}.json"
            )
            _write_once(
                verification_path,
                _json_raw(verification_document),
                "independent verification artifact",
            )
            _assert_formal_chain_unchanged(
                source_root,
                formal_chain_fingerprint,
            )
            receipt_body = {
                **_receipt_body(verification_sha256=verification_sha256),
                "completion_sha256": inputs["completion_sha256"],
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
            receipt_path = (
                run_root / RECEIPT_ROOT_NAME / f"{receipt_sha256}.json"
            )
            _write_once(
                receipt_path,
                _json_raw(receipt_document),
                "independent verification receipt",
            )
        _assert_formal_chain_unchanged(
            source_root,
            formal_chain_fingerprint,
        )
        status = {
            **_status_payload(
                verified=True,
                receipt_sha256=receipt_sha256,
                error_type=None,
            ),
            "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
            "completion_sha256": inputs["completion_sha256"],
            "receipt_path": (
                f"{RECEIPT_ROOT_NAME}/{receipt_sha256}.json"
            ),
        }
        _write_once(
            status_path,
            _json_raw(status),
            "independent verification status",
        )
        return {"status": "completed", "receipt_sha256": receipt_sha256}
    except BaseException as exc:
        if not status_path.exists():
            failed = {
                **_status_payload(
                    verified=False,
                    receipt_sha256=None,
                    error_type=type(exc).__name__,
                ),
                "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
                "completion_sha256": inputs.get("completion_sha256"),
                "receipt_path": None,
            }
            _write_once(
                status_path,
                _json_raw(failed),
                "independent verification status",
            )
        raise


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
            _load_formal_inputs(args.source_root)
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
