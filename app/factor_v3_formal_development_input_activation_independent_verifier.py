"""Structural differential replay for Factor V3 input activation publications."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Iterator, Mapping

from app import audited_pit_factor_v3_points_contract as points_contract
from app import factor_v2_decision_branch_selector as factor_v2_branch_selector
from app import (
    factor_v3_formal_development_input_activation_independent_core as independent_core,
)


INDEPENDENT_VERIFIER_PRODUCER_SCHEMA = (
    "factor-v3-formal-development-input-activation-independent-verifier-producer/v1"
)
_AUTHORITY_VERDICT_DIRECTORY = "authority-verdicts"
ACTIVATION_SCHEMA = "factor-v3-formal-development-input-activation/v1"
PUBLICATION_SCHEMA = "factor-v3-formal-development-input-activation-publication/v1"
INDEPENDENT_VERIFIER_RECEIPT_SCHEMA = (
    "factor-v3-formal-development-input-activation-independent-verifier-receipt/v1"
)
ACTIVATION_ELIGIBILITY_DESCRIPTOR_SCHEMA = (
    "factor-v3-formal-development-input-activation-eligibility-descriptor/v1"
)
FORMAL_AUTHORITY_SCOPE = "FORMAL_FROZEN_POINTS_CONTRACT"
DISPOSABLE_TEST_AUTHORITY_SCOPE = "DISPOSABLE_TEST_FIXTURE_ONLY"
_REGISTERED_FORMAL_INDEPENDENT_TCB = None
PARENT_PROJECTION_SCHEMA = "factor-v3-parent-row-projection-roots/v1"
PARENT_PROJECTION_FIELDS = (
    "candidate_keys_sha256",
    "source_feature_projection_rows_sha256",
    "full_export_rows_sha256",
)
PARENT_PROJECTION_CONTRACT = {
    "candidate_keys": {"fields": ["candidate_key"], "order": ["candidate_key"]},
    "source_feature_projection_rows": {
        "fields": ["candidate_key", "signal_date", "features"],
        "order": ["signal_date", "candidate_key"],
    },
    "full_export_rows": {
        "fields": ["candidate_key", "features", "signal_date", "ts_code"],
        "order": ["signal_date", "candidate_key"],
    },
}
FACTOR_V2_EVALUATION_PROJECTION_FIELDS = (
    "terminal_decision_descriptor_sha256",
    "evaluator_descriptor_sha256",
    "cost_slippage_execution_descriptor_sha256",
)
FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS = (
    "branch_receipt_sha256",
    "decision_receipt_sha256",
    "decision_receipt_raw_file_sha256",
    "evaluation_artifact_sha256",
    "selected_branch",
    "snapshot_schema",
)
FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA = (
    "factor-v2-terminal-evaluator-formal-development-authority-receipt/v1"
)
FACTOR_V2_EVALUATION_PROJECTION_SCHEMA = (
    "factor-v2-formal-development-evaluation-projection/v1"
)
POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-points-contract-authority-verdict/v1"
)
FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-feature-history-native-authority-verdict/v1"
)
DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-daily-basic-native-authority-verdict/v1"
)
PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-parent-source-native-authority-verdict/v1"
)
FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v2-evaluation-native-authority-verdict/v1"
)
PARENT_SOURCE_ATTEMPT_KEY_SCHEMA = (
    "factor-v3-parent-source-development-authority-attempt-key/v1"
)
PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA = (
    "factor-v3-parent-source-global-attempt-identity/v1"
)
PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION = (
    "factor-v3-parent-source-machine-global-root-lease/v1"
)
PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE = "attempts/sha256/{prefix}/{attempt_key}"
PARENT_SOURCE_EPOCH_FILE_NAMES = (
    "run.claim.json",
    "run.receipt.json",
    "verify.claim.json",
    "terminal.receipt.json",
)
PARENT_SOURCE_EPOCH_FILE_CONTRACT = {
    "run.claim.json": {
        "action": 1,
        "fields": (
            "action", "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-run-claim/v1",
        "state": 1,
    },
    "run.receipt.json": {
        "fields": (
            "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_claim_sha256", "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-run-receipt/v1",
        "state": 2,
    },
    "verify.claim.json": {
        "action": 2,
        "fields": (
            "action", "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_receipt_sha256", "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-verify-claim/v1",
        "state": 3,
    },
    "terminal.receipt.json": {
        "fields": (
            "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_receipt_sha256", "run_spec_sha256", "schema", "state",
            "verify_claim_sha256",
        ),
        "schema": "factor-v3-parent-source-root-terminal-receipt/v1",
        "state": 4,
    },
}
SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible", "embargo_consumed", "experiment_launch_eligible",
    "final_oos_consumed", "formal_materialization_performed", "model_training_started",
    "oof_scoring_started", "orders_submitted", "production_profile_registered",
    "production_recommendation_eligible", "recommendation_generation_eligible",
)
UPSTREAM_SOURCE_SEGMENTS = (
    "BSE", "SSE_MAIN", "SSE_STAR", "SZSE_CHINEXT", "SZSE_MAIN",
)
ACTIVATION_INPUT_BINDING_FIELDS = (
    "attempt_key_sha256", "candidate_authority_root_sha256",
    "candidate_descriptor_sha256", "candidate_publication_file_sha256",
    "daily_basic_authority_receipt_file_sha256", "daily_basic_authority_receipt_path",
    "factor_v2_evaluation_authority_receipt_file_sha256",
    "factor_v2_evaluation_authority_receipt_root_sha256",
    "feature_history_collection_issuance_file_sha256",
    "feature_history_collection_issuance_path",
    "feature_history_frozen_attestation_file_sha256",
    "feature_history_frozen_attestation_path", "global_attempt_identity_sha256",
    "global_attempt_ledger_root", "global_run_claim_path", "global_run_receipt_path",
    "global_terminal_receipt_path", "global_verify_claim_path",
    "native_lease_identity_sha256", "native_lease_policy_version",
    "parent_source_authority_receipt_file_sha256",
    "parent_source_authority_receipt_root_sha256",
    "parent_source_terminal_receipt_file_sha256", "run_spec_sha256",
    "semantic_input_root_sha256",
)
_ACTIVATION_DIRECTORY = "input-authorities"
_PUBLICATION_DIRECTORY = "publications"
_VERIFIER_RECEIPT_DIRECTORY = "receipts"


class IndependentActivationVerifierError(ValueError):
    pass


def _fail(message: str) -> None:
    raise IndependentActivationVerifierError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _descriptor_relative_path(root: str) -> str:
    return f"{_ACTIVATION_DIRECTORY}/sha256/{root[:2]}/{root}/input-authority.json"


def _publication_relative_path(root: str) -> str:
    return f"{_PUBLICATION_DIRECTORY}/sha256/{root[:2]}/{root}.json"


def _receipt_relative_path(root: str) -> str:
    return f"{_VERIFIER_RECEIPT_DIRECTORY}/sha256/{root[:2]}/{root}.json"


def _strict_sha(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == "0" * 64
    ):
        _fail(f"{label} rejected")
    return value


def _assert_fields(value: Any, fields: set[str] | frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        _fail(f"{label} fields rejected")
    return value


def _assert_false(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            _fail(f"{label} {field} must remain false")


def _assert_true(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not True:
            _fail(f"{label} {field} must be true")


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _is_reparse(path: Path) -> bool:
    info = os.lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _assert_safe_existing(path: Path, *, label: str, directory: bool | None = None) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise IndependentActivationVerifierError(f"{label} unavailable") from exc
    if _path_key(path) != _path_key(canonical):
        _fail(f"{label} must be canonical and non-reparse")
    cursor = path
    while True:
        if _is_reparse(cursor):
            _fail(f"{label} reparse path rejected")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if directory is True and not path.is_dir():
        _fail(f"{label} directory required")
    if directory is False and not path.is_file():
        _fail(f"{label} regular file required")
    return path


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(f"{label} duplicate key rejected")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite {token}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise IndependentActivationVerifierError(f"{label} JSON rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        _fail(f"{label} canonical JSON rejected")
    return value


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    _assert_safe_existing(path, label=label, directory=False)
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise IndependentActivationVerifierError(f"{label} read rejected") from exc
    fingerprint_before = (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
    )
    fingerprint_after = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    )
    if fingerprint_before != fingerprint_after or len(raw) != before.st_size:
        _fail(f"{label} changed during read")
    return _strict_json(raw, label=label), raw


def _read_cas(
    path_value: str | Path,
    expected_sha_value: str,
    *,
    category: str,
    label: str,
) -> tuple[Path, dict[str, Any], bytes, str]:
    path = Path(path_value)
    expected_sha = _strict_sha(expected_sha_value, label=f"expected {label} SHA")
    _assert_safe_existing(path, label=label, directory=False)
    if (
        path.name != f"{expected_sha}.json"
        or path.parent.name != expected_sha[:2]
        or path.parent.parent.name != "sha256"
        or path.parent.parent.parent.name != category
    ):
        _fail(f"{label} content-addressed path rejected")
    payload, raw = _read_json(path, label=label)
    if _sha256_bytes(raw) != expected_sha:
        _fail(f"{label} SHA mismatch")
    return path, payload, raw, expected_sha


def _relative_path(root: Path, relative: Any, *, label: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative:
        _fail(f"{label} relative path rejected")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{label} relative path rejected")
    path = root.joinpath(*parts)
    _assert_safe_existing(path, label=label, directory=False)
    if not path.is_relative_to(root):
        _fail(f"{label} escaped root")
    return path


def _overlap(left: Path, right: Path) -> bool:
    left_key = _path_key(left.resolve(strict=False))
    right_key = _path_key(right.resolve(strict=False))
    separator = os.sep
    return (
        left_key == right_key
        or left_key.startswith(right_key.rstrip(separator) + separator)
        or right_key.startswith(left_key.rstrip(separator) + separator)
    )


def _assert_isolated(root: Path, forbidden: Mapping[str, Path], *, label: str) -> None:
    if not root.is_absolute():
        _fail(f"{label} must be absolute")
    for name, other in forbidden.items():
        if _overlap(root, other):
            _fail(f"{label} overlaps {name} root")


def _self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    unsigned = dict(payload)
    root = _strict_sha(unsigned.pop("receipt_root_sha256", None), label=f"{label} root")
    if _canonical_sha256(unsigned) != root:
        _fail(f"{label} self hash rejected")
    return root


def _test_verdict(evidence: Mapping[str, Any], schema: str) -> dict[str, Any]:
    return {
        **deepcopy(dict(evidence)),
        "authority_scope": DISPOSABLE_TEST_AUTHORITY_SCOPE,
        "contract_binding_validated": True,
        "schema": schema,
        "test_fixture_only": True,
        "verified": False,
    }


def _producer_identity() -> dict[str, Any]:
    dependencies = {}
    for name, module in (
        ("factor_v2_branch_contract", factor_v2_branch_selector),
        ("independent_core", independent_core),
        ("independent_verifier", __import__(__name__, fromlist=["*"])),
        ("points_contract", points_contract),
    ):
        source = Path(module.__file__).resolve(strict=True)
        _assert_safe_existing(source, label=f"{name} producer source", directory=False)
        with source.open("rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(raw) != before.st_size
        ):
            _fail(f"{name} producer source changed during identity read")
        dependencies[name] = {
            "module": module.__name__,
            "path": f"{module.__name__.replace('.', '/')}.py",
            "sha256": _sha256_bytes(raw),
        }
    identity = {
        "builder": "independent-differential-reconstruction-v2",
        "dependencies": dependencies,
        "publisher_builder_reuse_permitted": False,
        "schema": INDEPENDENT_VERIFIER_PRODUCER_SCHEMA,
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


# Compatibility export: callers should bind the runtime identity mapping in the receipt.
INDEPENDENT_VERIFIER_PRODUCER_IDENTITY_SHA256 = _producer_identity()[
    "root_sha256"
]


def _differential_contract_check(replay: Mapping[str, Any]) -> None:
    candidate = replay["candidate"]
    snapshots = candidate["snapshot_payloads"]
    calendar = snapshots["calendar"]
    all_sessions = calendar["all_market_sessions"]
    prewindow = calendar["prewindow_sessions"]
    development = calendar["development_sessions"]
    source_dates = calendar["source_dates"]
    if (
        len(all_sessions) != 733
        or len(prewindow) != 250
        or len(development) != 483
        or len(source_dates) != 732
        or all_sessions != [*prewindow, *development]
        or source_dates != all_sessions[:-1]
        or calendar["all_market_sessions_sha256"]
        != _canonical_sha256(all_sessions)
        or calendar["prewindow_sessions_sha256"] != _canonical_sha256(prewindow)
        or calendar["development_sessions_sha256"]
        != _canonical_sha256(development)
        or calendar["source_dates_sha256"] != _canonical_sha256(source_dates)
    ):
        _fail("independent calendar differential replay rejected")

    rows = snapshots["factor_v2_parent"]["rows"]
    ordered_rows = sorted(
        rows, key=lambda row: (row["signal_date"], row["candidate_key"])
    )
    candidate_keys = sorted(row["candidate_key"] for row in rows)
    source_rows = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
            "features": row["features"],
        }
        for row in rows
    ]
    projection = {
        "schema": PARENT_PROJECTION_SCHEMA,
        "contract": deepcopy(PARENT_PROJECTION_CONTRACT),
        "candidate_keys_sha256": _canonical_sha256(candidate_keys),
        "source_feature_projection_rows_sha256": _canonical_sha256(source_rows),
        "full_export_rows_sha256": _canonical_sha256(ordered_rows),
    }
    if (
        rows != ordered_rows
        or candidate["parent_projection"] != projection
        or replay["parent"]["receipt"]["parent_projection"] != projection
        or any(row["signal_date"] not in set(development) for row in rows)
    ):
        _fail("independent parent projection differential replay rejected")

    board = snapshots["upstream_board_ledger"]
    if (
        board["per_date_board_ledger_root_sha256"]
        != _canonical_sha256(board["per_date"])
        or [entry["trade_date"] for entry in board["per_date"]] != source_dates
    ):
        _fail("independent board differential replay rejected")

    daily = snapshots["daily_basic_exact_set_receipt"][
        "source_receipt_projection"
    ]
    statistics = daily["per_date_statistics"]
    if (
        daily["trade_dates"] != all_sessions
        or daily["trade_dates_sha256"] != _canonical_sha256(all_sessions)
        or daily["per_date_statistics_sha256"] != _canonical_sha256(statistics)
        or [entry["trade_date"] for entry in statistics] != all_sessions
    ):
        _fail("independent daily authority differential replay rejected")

    evaluation_receipt = json.loads(
        replay["evaluation"]["receipt_raw"].decode("utf-8")
    )
    if (
        replay["evaluation"]["binding"] != candidate["evaluation_binding"]
        or evaluation_receipt["evaluation_projection"]
        != replay["evaluation"]["projection"]
    ):
        _fail("independent evaluation differential replay rejected")

    parent = replay["parent"]
    epoch_hashes = {
        name: _sha256_bytes(raw) for name, raw in parent["epoch_raw"].items()
    }
    epoch_payloads = {
        name: json.loads(raw.decode("utf-8"))
        for name, raw in parent["epoch_raw"].items()
    }
    if (
        epoch_payloads["run.receipt.json"]["run_claim_sha256"]
        != epoch_hashes["run.claim.json"]
        or epoch_payloads["verify.claim.json"]["run_receipt_sha256"]
        != epoch_hashes["run.receipt.json"]
        or epoch_payloads["terminal.receipt.json"]["run_receipt_sha256"]
        != epoch_hashes["run.receipt.json"]
        or epoch_payloads["terminal.receipt.json"]["verify_claim_sha256"]
        != epoch_hashes["verify.claim.json"]
    ):
        _fail("independent epoch differential replay rejected")


def _differential_replay_inputs(
    *,
    candidate_output_root: str | Path,
    candidate_publication_path: str | Path,
    expected_candidate_publication_sha256: str,
    parent_source_authority_receipt_path: str | Path,
    expected_parent_source_authority_receipt_sha256: str,
    parent_source_terminal_epoch_receipt_path: str | Path,
    expected_parent_source_terminal_epoch_receipt_sha256: str,
    factor_v2_evaluation_authority_receipt_path: str | Path,
    expected_factor_v2_evaluation_authority_receipt_sha256: str,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
) -> dict[str, Any]:
    try:
        replay = independent_core.replay_inputs(
            candidate_output_root=candidate_output_root,
            candidate_publication_path=candidate_publication_path,
            expected_candidate_publication_sha256=expected_candidate_publication_sha256,
            parent_source_authority_receipt_path=parent_source_authority_receipt_path,
            expected_parent_source_authority_receipt_sha256=expected_parent_source_authority_receipt_sha256,
            parent_source_terminal_epoch_receipt_path=parent_source_terminal_epoch_receipt_path,
            expected_parent_source_terminal_epoch_receipt_sha256=expected_parent_source_terminal_epoch_receipt_sha256,
            factor_v2_evaluation_authority_receipt_path=factor_v2_evaluation_authority_receipt_path,
            expected_factor_v2_evaluation_authority_receipt_sha256=expected_factor_v2_evaluation_authority_receipt_sha256,
            feature_history_collection_issuance_path=feature_history_collection_issuance_path,
            expected_feature_history_collection_issuance_sha256=expected_feature_history_collection_issuance_sha256,
            feature_history_frozen_attestation_path=feature_history_frozen_attestation_path,
            expected_feature_history_frozen_attestation_sha256=expected_feature_history_frozen_attestation_sha256,
            daily_basic_authority_receipt_path=daily_basic_authority_receipt_path,
            expected_daily_basic_authority_receipt_sha256=expected_daily_basic_authority_receipt_sha256,
        )
    except independent_core.IndependentCoreError as exc:
        raise IndependentActivationVerifierError(str(exc)) from exc
    _differential_contract_check(replay)
    return replay

def _authority_verdict_descriptors(
    replay: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    descriptors: dict[str, dict[str, str]] = {}
    for name, verdict in sorted(replay["authority_verdicts"].items()):
        digest = _sha256_bytes(_canonical_bytes(verdict))
        descriptors[name] = {
            "file_sha256": digest,
            "relative_path": (
                f"{_AUTHORITY_VERDICT_DIRECTORY}/sha256/{digest[:2]}/{digest}.json"
            ),
            "schema": verdict["schema"],
            "verdict_root_sha256": _canonical_sha256(verdict),
        }
    return descriptors


def _build_descriptor(replay: Mapping[str, Any]) -> dict[str, Any]:
    candidate = replay["candidate"]
    parent = replay["parent"]["receipt"]
    evaluation = replay["evaluation"]
    formal = candidate["authority_scope"] == FORMAL_AUTHORITY_SCOPE
    return {
        "activation_verified": formal,
        "activation_schema": ACTIVATION_SCHEMA,
        "authority_scope": candidate["authority_scope"],
        "authority_verifier_bindings": _authority_verdict_descriptors(replay),
        "authority_status": (
            "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT"
            if formal
            else "DISPOSABLE_TEST_FIXTURE_CONTRACT_REPLAY_ONLY"
        ),
        "calendar": {
            key: value
            for key, value in candidate["calendar"].items()
            if key not in {"source_dates", "prewindow_sessions"}
        },
        "development_only": True,
        "factor_v2_evaluation_projection": deepcopy(evaluation["projection"]),
        "factor_v2_evaluation_source_binding": deepcopy(evaluation["binding"]),
        "contract_binding_validated": True,
        "formal_materialization_eligible": formal,
        "input_bindings": deepcopy(replay["input_bindings"]),
        "machine_global_root_lease_verified": formal,
        "materializer_v1_compatible": False,
        "materializer_compatibility_reason": (
            "candidate-v3-public-projections-require-factor-v3-materializer-v2"
        ),
        "parent_payload_fields": ["features"],
        "parent_projection": deepcopy(candidate["parent_projection"]),
        "parent_source_authority_verified": formal,
        "points_contract_common_eligible_projection": deepcopy(
            parent["points_contract_common_eligible_projection"]
        ),
        "root_epoch_terminal_verified": formal,
        "schema_version": ACTIVATION_ELIGIBILITY_DESCRIPTOR_SCHEMA,
        "source_authority_complete": formal,
        "source_descriptors": deepcopy(candidate["descriptor"]["snapshots"]),
        "test_fixture_only": (
            candidate["authority_scope"] == DISPOSABLE_TEST_AUTHORITY_SCOPE
        ),
        "upstream_board_projection": deepcopy(candidate["board_projection"]),
        "verified": formal,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }


def _build_manifest(
    descriptor: Mapping[str, Any]
) -> tuple[dict[str, Any], str, str]:
    descriptor_raw = _canonical_bytes(descriptor)
    activation_root = _sha256_bytes(descriptor_raw)
    descriptor_relative = _descriptor_relative_path(activation_root)
    manifest = {
        "activation_verified": descriptor["activation_verified"],
        "activation_root_sha256": activation_root,
        "authority_scope": descriptor["authority_scope"],
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": activation_root,
        "development_only": True,
        "contract_binding_validated": True,
        "formal_materialization_eligible": descriptor[
            "formal_materialization_eligible"
        ],
        "materializer_v1_compatible": False,
        "materializer_compatibility_reason": (
            "candidate-v3-public-projections-require-factor-v3-materializer-v2"
        ),
        "schema": PUBLICATION_SCHEMA,
        "source_authority_complete": descriptor["source_authority_complete"],
        "test_fixture_only": (
            descriptor["authority_scope"]
            == DISPOSABLE_TEST_AUTHORITY_SCOPE
        ),
        "verified": descriptor["verified"],
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return manifest, activation_root, descriptor_relative


def _activation_files(
    *,
    output_root: Path,
    descriptor: Mapping[str, Any],
    manifest: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> list[tuple[Path, bytes, str]]:
    descriptor_raw = _canonical_bytes(descriptor)
    activation_root = _sha256_bytes(descriptor_raw)
    descriptor_path = output_root.joinpath(
        *_descriptor_relative_path(activation_root).split("/")
    )
    manifest_raw = _canonical_bytes(manifest)
    publication_path = output_root.joinpath(
        *_publication_relative_path(_sha256_bytes(manifest_raw)).split("/")
    )
    files = [
        (descriptor_path, descriptor_raw, "activation descriptor"),
        (publication_path, manifest_raw, "activation publication"),
    ]
    files.extend(
        (
            descriptor_path.parent / f"{name}.json",
            raw,
            f"activated source snapshot {name}",
        )
        for name, raw in replay["candidate"]["snapshot_raw"].items()
    )
    verdict_descriptors = descriptor["authority_verifier_bindings"]
    files.extend(
        (
            output_root.joinpath(
                *verdict_descriptors[name]["relative_path"].split("/")
            ),
            _canonical_bytes(verdict),
            f"authority verdict {name}",
        )
        for name, verdict in replay["authority_verdicts"].items()
    )
    return files


def _activation_output_root(publication_path: Path, expected_sha: str) -> Path:
    _assert_safe_existing(
        publication_path, label="activation publication", directory=False
    )
    if (
        publication_path.name != f"{expected_sha}.json"
        or publication_path.parent.name != expected_sha[:2]
        or publication_path.parent.parent.name != "sha256"
        or publication_path.parent.parent.parent.name != _PUBLICATION_DIRECTORY
    ):
        _fail("activation publication CAS path rejected")
    return publication_path.parents[3]


def _forbidden_roots(replay: Mapping[str, Any]) -> dict[str, Path]:
    roots = {
        "candidate": replay["candidate"]["root"],
        "parent authority": replay["parent"]["path"].parents[3],
        "evaluation authority": replay["evaluation"]["path"].parents[3],
        "machine-global epoch": replay["parent"]["ledger_root"],
    }
    roots.update(
        {
            f"{name} source authority": receipt["path"].parents[3]
            for name, receipt in replay["candidate"][
                "source_authority_receipts"
            ].items()
        }
    )
    return roots


def _input_files(replay: Mapping[str, Any]) -> list[tuple[Path, bytes, str]]:
    candidate = replay["candidate"]
    files = [
        (candidate["publication_path"], candidate["manifest_raw"], "candidate publication"),
        (candidate["descriptor_path"], candidate["descriptor_raw"], "candidate descriptor"),
        (replay["parent"]["path"], replay["parent"]["receipt_raw"], "parent receipt"),
        (replay["evaluation"]["path"], replay["evaluation"]["receipt_raw"], "evaluation receipt"),
    ]
    files.extend(
        (
            candidate["descriptor_path"].parent / f"{name}.json",
            raw,
            f"candidate snapshot {name}",
        )
        for name, raw in candidate["snapshot_raw"].items()
    )
    files.extend(
        (
            replay["parent"]["epoch_paths"][name],
            raw,
            f"parent epoch {name}",
        )
        for name, raw in replay["parent"]["epoch_raw"].items()
    )
    files.extend(
        (receipt["path"], receipt["raw"], f"source authority {name}")
        for name, receipt in candidate["source_authority_receipts"].items()
    )
    return files


class _HeldFile:
    def __init__(self, path: Path, raw: bytes, label: str) -> None:
        self.path = path
        self.raw = raw
        self.label = label
        self.stream = path.open("rb")
        info = os.fstat(self.stream.fileno())
        self.fingerprint = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
        if self.stream.read() != raw:
            self.stream.close()
            _fail(f"{label} changed before hold")

    def postverify(self) -> None:
        info = os.fstat(self.stream.fileno())
        fingerprint = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
        self.stream.seek(0)
        if fingerprint != self.fingerprint or self.stream.read() != self.raw:
            _fail(f"{self.label} changed during independent replay")
        _payload, raw = _read_json(self.path, label=self.label)
        if raw != self.raw:
            _fail(f"{self.label} path changed during independent replay")

    def close(self) -> None:
        self.stream.close()


def _hold(files: list[tuple[Path, bytes, str]]) -> list[_HeldFile]:
    held: list[_HeldFile] = []
    try:
        for path, raw, label in files:
            held.append(_HeldFile(path, raw, label))
    except BaseException:
        _close_all(held)
        raise
    return held


def _close_all(held: list[_HeldFile]) -> None:
    first: BaseException | None = None
    for item in reversed(held):
        try:
            item.close()
        except BaseException as exc:
            if first is None:
                first = exc
    if first is not None:
        raise first


def _safe_output_root(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_existing(path.parent, label=f"{label} parent", directory=True)
    path.mkdir(exist_ok=True)
    return _assert_safe_existing(path, label=label, directory=True)


@contextmanager
def _locked_output_root(root: Path) -> Iterator[None]:
    lock_path = root / ".factor-v3-independent-verifier.lock"
    stream = lock_path.open("a+b")
    try:
        stream.seek(0)
        if stream.read(1) != b"\0":
            stream.seek(0)
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _directory_identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


@contextmanager
def _held_directory(path: Path, *, label: str) -> Iterator[None]:
    _assert_safe_existing(path, label=label, directory=True)
    before = _directory_identity(path)
    handle: int | None = None
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        opened = create_file(
            str(path),
            0x80000000,
            0x00000003,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if opened == ctypes.c_void_p(-1).value:
            _fail(f"{label} directory handle unavailable")
        handle = int(opened)
        if _is_reparse(path) or _directory_identity(path) != before:
            kernel32.CloseHandle(handle)
            _fail(f"{label} directory changed during handle acquisition")
    else:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        handle = os.open(path, flags)
    try:
        yield
    finally:
        if handle is not None:
            if os.name == "nt":
                import ctypes

                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            else:
                os.close(handle)
        if _directory_identity(path) != before:
            _fail(f"{label} directory changed during create-only transaction")


def _write_create_only(path: Path, raw: bytes, *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_existing(path.parent, label=f"{label} parent", directory=True)
    with _held_directory(path.parent, label=f"{label} parent"):
        _write_create_only_locked(path, raw, label=label)


def _write_create_only_locked(path: Path, raw: bytes, *, label: str) -> None:
    if path.exists():
        _payload, existing = _read_json(path, label=label)
        if existing != raw:
            _fail(f"{label} create-once conflict")
        return
    temporary = path.parent / f".factor-v3-independent-{secrets.token_hex(16)}.tmp"
    linked = False
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        linked = True
        try:
            temporary.unlink()
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise IndependentActivationVerifierError(
                f"{label} temporary alias cleanup failed"
            ) from exc
        _payload, existing = _read_json(path, label=label)
        if existing != raw:
            _fail(f"{label} create-only postverification rejected")
    except FileExistsError:
        _payload, existing = _read_json(path, label=label)
        if existing != raw:
            _fail(f"{label} create-once conflict")
    finally:
        if temporary.exists():
            temporary.unlink()
        if linked and temporary.exists():
            path.unlink(missing_ok=True)


def verify_factor_v3_formal_development_input_activation_independently(
    *,
    candidate_output_root: str | Path,
    candidate_publication_path: str | Path,
    expected_candidate_publication_sha256: str,
    parent_source_authority_receipt_path: str | Path,
    expected_parent_source_authority_receipt_sha256: str,
    parent_source_terminal_epoch_receipt_path: str | Path,
    expected_parent_source_terminal_epoch_receipt_sha256: str,
    factor_v2_evaluation_authority_receipt_path: str | Path,
    expected_factor_v2_evaluation_authority_receipt_sha256: str,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
    activation_publication_path: str | Path,
    expected_activation_publication_sha256: str,
    verifier_output_root: str | Path,
    allow_disposable_test_fixture: bool,
) -> dict[str, Any]:
    if not allow_disposable_test_fixture:
        if _REGISTERED_FORMAL_INDEPENDENT_TCB is None:
            _fail("formal independent TCB unavailable")
        _fail("formal independent TCB is not versioned for this verifier")
    replay = _differential_replay_inputs(
        candidate_output_root=candidate_output_root,
        candidate_publication_path=candidate_publication_path,
        expected_candidate_publication_sha256=expected_candidate_publication_sha256,
        parent_source_authority_receipt_path=parent_source_authority_receipt_path,
        expected_parent_source_authority_receipt_sha256=expected_parent_source_authority_receipt_sha256,
        parent_source_terminal_epoch_receipt_path=parent_source_terminal_epoch_receipt_path,
        expected_parent_source_terminal_epoch_receipt_sha256=expected_parent_source_terminal_epoch_receipt_sha256,
        factor_v2_evaluation_authority_receipt_path=factor_v2_evaluation_authority_receipt_path,
        expected_factor_v2_evaluation_authority_receipt_sha256=expected_factor_v2_evaluation_authority_receipt_sha256,
        feature_history_collection_issuance_path=feature_history_collection_issuance_path,
        expected_feature_history_collection_issuance_sha256=expected_feature_history_collection_issuance_sha256,
        feature_history_frozen_attestation_path=feature_history_frozen_attestation_path,
        expected_feature_history_frozen_attestation_sha256=expected_feature_history_frozen_attestation_sha256,
        daily_basic_authority_receipt_path=daily_basic_authority_receipt_path,
        expected_daily_basic_authority_receipt_sha256=expected_daily_basic_authority_receipt_sha256,
    )
    scope = replay["candidate"]["authority_scope"]
    if allow_disposable_test_fixture:
        if scope != DISPOSABLE_TEST_AUTHORITY_SCOPE:
            _fail("disposable independent replay requires disposable test scope")
    elif scope != FORMAL_AUTHORITY_SCOPE:
        _fail("public formal independent replay rejects disposable test authority scope")
    publication_sha = _strict_sha(
        expected_activation_publication_sha256,
        label="expected activation publication SHA",
    )
    publication_path = Path(activation_publication_path)
    activation_root_path = _activation_output_root(publication_path, publication_sha)
    _assert_safe_existing(activation_root_path, label="activation root", directory=True)
    _assert_isolated(activation_root_path, _forbidden_roots(replay), label="activation root")
    publication, publication_raw = _read_json(publication_path, label="activation publication")
    if _sha256_bytes(publication_raw) != publication_sha:
        _fail("activation publication SHA mismatch")
    descriptor = _build_descriptor(replay)
    manifest, activation_root, descriptor_relative = _build_manifest(descriptor)
    if publication != manifest:
        _fail("activation publication independent differential mismatch")
    descriptor_path = _relative_path(
        activation_root_path, descriptor_relative, label="activation descriptor"
    )
    actual_descriptor, descriptor_raw = _read_json(descriptor_path, label="activation descriptor")
    if actual_descriptor != descriptor or _sha256_bytes(descriptor_raw) != activation_root:
        _fail("activation descriptor independent differential mismatch")
    expected_names = {
        "input-authority.json",
        *(f"{name}.json" for name in independent_core.CANDIDATE_SNAPSHOT_NAMES),
    }
    if {child.name for child in descriptor_path.parent.iterdir()} != expected_names:
        _fail("activation descriptor namespace rejected")
    for path, expected_raw, label in _activation_files(
        output_root=activation_root_path,
        descriptor=descriptor,
        manifest=manifest,
        replay=replay,
    ):
        _payload, actual_raw = _read_json(path, label=label)
        if actual_raw != expected_raw:
            _fail(f"{label} independent differential mismatch")
    verifier_root = Path(verifier_output_root)
    _assert_isolated(
        verifier_root,
        {**_forbidden_roots(replay), "activation": activation_root_path},
        label="independent verifier output root",
    )
    verifier_root = _safe_output_root(
        verifier_root, label="independent verifier output root"
    )
    producer_identity = _producer_identity()
    receipt = {
        "activation_verified": descriptor["activation_verified"],
        "activation_descriptor_file_sha256": activation_root,
        "activation_publication_file_sha256": publication_sha,
        "activation_root_sha256": activation_root,
        "authority_scope": descriptor["authority_scope"],
        "contract_binding_validated": True,
        "development_only": True,
        "differential_contract_replay_performed": True,
        "formal_materialization_eligible": descriptor["formal_materialization_eligible"],
        "independent_public_replay_performed": False,
        "independent_verifier_producer_identity": producer_identity,
        "independent_verifier_producer_identity_sha256": producer_identity["root_sha256"],
        "materializer_v1_compatible": False,
        "materializer_compatibility_reason": "candidate-v3-public-projections-require-factor-v3-materializer-v2",
        "replayed_input_bindings": deepcopy(replay["input_bindings"]),
        "schema": INDEPENDENT_VERIFIER_RECEIPT_SCHEMA,
        "source_authority_complete": descriptor["source_authority_complete"],
        "test_fixture_only": scope == DISPOSABLE_TEST_AUTHORITY_SCOPE,
        "verified": descriptor["verified"],
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    receipt_raw = _canonical_bytes(receipt)
    receipt_sha = _sha256_bytes(receipt_raw)
    receipt_relative = _receipt_relative_path(receipt_sha)
    receipt_path = verifier_root.joinpath(*receipt_relative.split("/"))
    held = _hold(
        [
            *_input_files(replay),
            *_activation_files(
                output_root=activation_root_path,
                descriptor=descriptor,
                manifest=manifest,
                replay=replay,
            ),
        ]
    )
    try:
        with _locked_output_root(verifier_root):
            for item in held:
                item.postverify()
            if _producer_identity() != producer_identity:
                _fail("independent verifier producer changed during replay")
            _write_create_only(
                receipt_path, receipt_raw, label="independent verifier receipt"
            )
    finally:
        _close_all(held)
    return {
        "differential_contract_replay_performed": True,
        "independent_public_replay_performed": False,
        "receipt_file_sha256": receipt_sha,
        "receipt_relative_path": receipt_relative,
        "schema": INDEPENDENT_VERIFIER_RECEIPT_SCHEMA,
        "verified": descriptor["verified"],
    }


__all__ = (
    "INDEPENDENT_VERIFIER_PRODUCER_IDENTITY_SHA256",
    "INDEPENDENT_VERIFIER_PRODUCER_SCHEMA",
    "verify_factor_v3_formal_development_input_activation_independently",
)
