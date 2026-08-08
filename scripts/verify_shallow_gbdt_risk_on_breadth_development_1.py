from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[1]
RUN_ROOT_RELATIVE = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_risk_on_breadth_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
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
ISOLATED_REPLAY_DRIVER = r'''
import json
import runpy
import sys

argv = json.loads(sys.argv[1])
if not isinstance(argv, list) or argv[:2] != ["-m", "app.jobs"]:
    raise SystemExit("isolated replay argv is invalid")
sys.argv[:] = ["app.jobs", *argv[2:]]
runpy.run_module("app.jobs", run_name="__main__", alter_sys=False)
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


def _verified_producer_binding(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PRODUCER_BINDING_FIELDS:
        raise ValueError("result bundle producer binding fields drifted")
    producer = dict(value)
    root_sha256 = _require_sha256(
        producer.pop("root_sha256"),
        "result bundle producer root",
    )
    for field in PRODUCER_BINDING_FIELDS - {"schema_version", "root_sha256"}:
        _require_sha256(producer.get(field), f"result bundle producer {field}")
    if (
        producer.get("schema_version") != EXPECTED_PRODUCER_SCHEMA
        or producer.get("risk_on_breadth_strategy_sha256")
        != EXPECTED_STRATEGY_SHA256
        or root_sha256 != EXPECTED_PRODUCER_ROOT_SHA256
        or _sha256(producer) != root_sha256
    ):
        raise ValueError("result bundle producer binding is invalid")
    return {**producer, "root_sha256": root_sha256}


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
        != EXPECTED_PRODUCER_ROOT_SHA256
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
        runtime = _verify_runtime_verification(
            run_root,
            expected_main_sha256=main_sha256,
            expected_sidecar_sha256=sidecar_hashes,
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
        producer = _verified_producer_binding(main_document.get("producer_code"))
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


def _validate_replay(
    inputs: Mapping[str, Any],
    replay: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    raise NotImplementedError


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
