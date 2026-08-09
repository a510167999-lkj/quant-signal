"""Fail-closed candidate contracts; no parent-source authority is registered."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping


SEMANTIC_INPUT_SCHEMA = (
    "factor-v3-parent-source-development-authority-semantic-input/v1"
)
RUN_SPEC_SCHEMA = "factor-v3-parent-source-development-authority-run-spec/v1"
ATTEMPT_KEY_SCHEMA = (
    "factor-v3-parent-source-development-authority-attempt-key/v1"
)
RUN_CLAIM_SCHEMA = "factor-v3-parent-source-development-authority-run-claim/v1"
VERIFICATION_CLAIM_SCHEMA = (
    "factor-v3-parent-source-development-authority-verification-claim/v1"
)
AUTHORITY_RECEIPT_SCHEMA = (
    "factor-v3-parent-source-development-authority-verification-receipt/v1"
)
CLAIM_PAIR_OBSERVATION_SCHEMA = (
    "factor-v3-parent-source-development-authority-claim-pair-observation/v1"
)
NATIVE_TCB_AUTHORITY_ARTIFACT_SCHEMA = (
    "factor-v3-parent-source-development-native-tcb-authority-artifact/v1"
)

# Populated only by a later, independently audited two-commit authority publication.
REGISTERED_GLOBAL_ATTEMPT_LEDGER_AUTHORITY_SHA256: str | None = None
REGISTERED_NATIVE_TCB_AUTHORITY_ARTIFACT_SHA256: str | None = None

AUTHORITY_GATE_FIELDS = (
    "candidate_run_verified",
    "git_tree_blob_binding_verified",
    "global_single_attempt_verified",
    "independent_replay_verified",
    "input_closure_verified",
    "loaded_source_identity_verified",
    "native_runtime_authority_verified",
    "producer_binding_verified",
    "python_runtime_authority_verified",
    "runtime_dependency_authority_verified",
    "venv_runtime_authority_verified",
)

SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "final_oos_consumed",
    "model_training_started",
    "oof_scoring_started",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
)

_HEX = frozenset("0123456789abcdef")
_SEMANTIC_INPUT_FIELDS = frozenset(
    {
        "candidate_contract_root_sha256",
        "control_contract_root_sha256",
        "frozen_source_blob_root_sha256",
        "frozen_source_commit",
        "frozen_source_tree_oid",
        "input_closure_root_sha256",
        "overlay_artifact_sha256",
        "parent_artifact_sha256",
        "producer_source_authority_sha256",
        "runtime_authority_root_sha256",
        "schema",
        "suspension_bundle_sha256",
    }
)
_PROOF_ROOT_FIELDS = (
    "global_run_claim_sha256",
    "global_verify_claim_sha256",
    "input_closure_root_sha256",
    "loaded_source_ledger_root_sha256",
    "producer_source_authority_sha256",
    "runtime_authority_root_sha256",
)
_PROOF_FIELDS = frozenset({*AUTHORITY_GATE_FIELDS, *_PROOF_ROOT_FIELDS})
_CLAIM_FIELDS = frozenset(
    {
        "action",
        "attempt_key_sha256",
        "claim_sha256",
        "development_only",
        "formal_materialization_eligible",
        "parent_source_authority_verified",
        "run_spec_sha256",
        "schema",
        "semantic_input_root_sha256",
        "single_attempt",
        "source_authority_complete",
        "source_authority_verified",
        "verified",
        *SAFETY_FALSE_FIELDS,
    }
)
_SPEC_FALSE_FIELDS = (
    "formal_materialization_eligible",
    "global_attempt_ledger_authority_verified",
    "parent_source_authority_verified",
    "source_authority_complete",
    "source_authority_verified",
    "verified",
    *SAFETY_FALSE_FIELDS,
)
_SPEC_FIELDS = frozenset(
    {
        "attempt_key_sha256",
        "authority_status",
        "candidate_run_spec_sha256",
        "development_only",
        "global_attempt_ledger_root",
        "global_run_claim_path",
        "global_verify_claim_path",
        "run_root",
        "run_spec_sha256",
        "schema",
        "semantic_input",
        "semantic_input_root_sha256",
        "verification_root",
        *_SPEC_FALSE_FIELDS,
    }
)


class FactorV3ParentSourceDevelopmentAuthorityError(ValueError):
    """Raised when a development parent-source authority proof fails closed."""


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
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "authority canonical JSON rejected"
        ) from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_sha256(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} SHA-256 rejected"
        )
    return value


def _strict_oid(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 40
        or any(character not in _HEX for character in value)
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} object ID rejected"
        )
    return value


def _reject_reparse_chain(path: Path, *, label: str) -> None:
    current = path
    while True:
        if current.exists() or current.is_symlink():
            value = os.lstat(current)
            attributes = getattr(value, "st_file_attributes", 0)
            if stat.S_ISLNK(value.st_mode) or attributes & 0x400:
                raise FactorV3ParentSourceDevelopmentAuthorityError(
                    f"{label} reparse path rejected"
                )
        if current == current.parent:
            return
        current = current.parent


def _absolute_path(value: str | Path, *, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise FactorV3ParentSourceDevelopmentAuthorityError(f"{label} path rejected")
    path = Path(value).resolve(strict=False)
    if not path.is_absolute():
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} absolute path required"
        )
    _reject_reparse_chain(path, label=label)
    return path


def _paths_overlap(left: Path, right: Path) -> bool:
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


def _reject_overlapping_roots(*roots: Path) -> None:
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if _paths_overlap(left, right):
                raise FactorV3ParentSourceDevelopmentAuthorityError(
                    "authority output and ledger roots overlap"
                )


def _validated_semantic_input(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _SEMANTIC_INPUT_FIELDS:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "semantic input fields rejected"
        )
    semantic = dict(value)
    if semantic.get("schema") != SEMANTIC_INPUT_SCHEMA:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "semantic input schema rejected"
        )
    for field in _SEMANTIC_INPUT_FIELDS - {
        "schema",
        "frozen_source_commit",
        "frozen_source_tree_oid",
    }:
        _strict_sha256(semantic[field], label=field)
    _strict_oid(semantic["frozen_source_commit"], label="frozen source commit")
    _strict_oid(semantic["frozen_source_tree_oid"], label="frozen source tree")
    return semantic


def _false_scope(value: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    if any(value.get(field) is not False for field in fields):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} false scope rejected"
        )


def _validated_spec(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _SPEC_FIELDS:
        raise FactorV3ParentSourceDevelopmentAuthorityError("run spec fields rejected")
    spec = dict(value)
    if (
        spec.get("schema") != RUN_SPEC_SCHEMA
        or spec.get("authority_status") != "CANDIDATE_AUTHORITY_CONTRACT_ONLY"
        or spec.get("development_only") is not True
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError("run spec scope rejected")
    _false_scope(spec, _SPEC_FALSE_FIELDS, label="run spec")
    semantic = _validated_semantic_input(spec["semantic_input"])
    semantic_root = _canonical_sha256(semantic)
    if spec.get("semantic_input_root_sha256") != semantic_root:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "semantic input root binding rejected"
        )
    attempt_key = _canonical_sha256(
        {
            "schema": ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
        }
    )
    if spec.get("attempt_key_sha256") != attempt_key:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "semantic attempt key binding rejected"
        )
    _strict_sha256(spec["candidate_run_spec_sha256"], label="candidate run spec")
    run_root = _absolute_path(spec["run_root"], label="run root")
    verification_root = _absolute_path(
        spec["verification_root"], label="verification root"
    )
    ledger_root = _absolute_path(
        spec["global_attempt_ledger_root"], label="global attempt ledger root"
    )
    _reject_overlapping_roots(run_root, verification_root, ledger_root)
    claim_root = ledger_root / "attempts" / "sha256" / attempt_key[:2] / attempt_key
    if Path(spec["global_run_claim_path"]) != claim_root / "run.claim.json":
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "global run claim path binding rejected"
        )
    if Path(spec["global_verify_claim_path"]) != claim_root / "verify.claim.json":
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "global verify claim path binding rejected"
        )
    unsigned = dict(spec)
    observed = _strict_sha256(unsigned.pop("run_spec_sha256"), label="run spec")
    if observed != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceDevelopmentAuthorityError("run spec self hash rejected")
    return spec


def _read_same_handle(path: Path, *, label: str) -> bytes:
    _reject_reparse_chain(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} open rejected"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FactorV3ParentSourceDevelopmentAuthorityError(
                f"{label} regular file required"
            )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > 65536:
                raise FactorV3ParentSourceDevelopmentAuthorityError(
                    f"{label} size rejected"
                )
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    raw = b"".join(chunks)
    if identity_before != identity_after or len(raw) != before.st_size:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{label} same-handle postread binding rejected"
        )
    return raw


def _json_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise FactorV3ParentSourceDevelopmentAuthorityError(
                "claim duplicate JSON key rejected"
            )
        value[key] = item
    return value


def _validated_claim_raw(
    raw: bytes,
    *,
    spec: Mapping[str, Any],
    action: str,
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{action} claim JSON rejected"
        ) from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{action} claim canonical bytes rejected"
        )
    if set(value) != _CLAIM_FIELDS:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{action} claim fields rejected"
        )
    expected_schema = RUN_CLAIM_SCHEMA if action == "run" else VERIFICATION_CLAIM_SCHEMA
    if (
        value.get("action") != action
        or value.get("schema") != expected_schema
        or value.get("development_only") is not True
        or value.get("single_attempt") is not True
        or value.get("attempt_key_sha256") != spec["attempt_key_sha256"]
        or value.get("semantic_input_root_sha256")
        != spec["semantic_input_root_sha256"]
        or value.get("run_spec_sha256") != spec["run_spec_sha256"]
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{action} claim binding rejected"
        )
    _false_scope(
        value,
        (
            "formal_materialization_eligible",
            "parent_source_authority_verified",
            "source_authority_complete",
            "source_authority_verified",
            "verified",
            *SAFETY_FALSE_FIELDS,
        ),
        label=f"{action} claim",
    )
    unsigned = dict(value)
    observed = _strict_sha256(unsigned.pop("claim_sha256"), label=f"{action} claim")
    if observed != _canonical_sha256(unsigned):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"{action} claim self hash rejected"
        )
    return value


def _validated_candidate_results(
    spec: Mapping[str, Any],
    run_result: Any,
    verification_result: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(run_result) is not dict or type(verification_result) is not dict:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "candidate receipts rejected"
        )
    run = dict(run_result)
    verification = dict(verification_result)
    required_run = {
        "candidate_root_sha256",
        "candidate_verified",
        "formal_materialization_eligible",
        "producer_binding_verified",
        "run_spec_sha256",
        "runtime_dependency_authority_verified",
        "schema",
        "source_authority_complete",
        "source_authority_verified",
        "verified",
    }
    required_verification = {
        "candidate_verified",
        "formal_materialization_eligible",
        "independent_public_replay_performed",
        "producer_binding_verified",
        "run_candidate_root_sha256",
        "run_spec_sha256",
        "runtime_dependency_authority_verified",
        "schema",
        "source_authority_complete",
        "source_authority_verified",
        "verification_root_sha256",
        "verified",
    }
    if not required_run.issubset(run) or not required_verification.issubset(
        verification
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "candidate receipt fields rejected"
        )
    from app import factor_v3_parent_source_authority_runner as candidate

    for result, label in (
        (run, "candidate run"),
        (verification, "candidate verification"),
    ):
        if any(
            field in result and result[field] is not False
            for field in candidate.SAFETY_FALSE_FIELDS
        ):
            raise FactorV3ParentSourceDevelopmentAuthorityError(
                f"{label} false scope rejected"
            )

    if (
        run.get("schema") != candidate.RESULT_SCHEMA
        or verification.get("schema") != candidate.VERIFICATION_RESULT_SCHEMA
        or run.get("candidate_verified") is not True
        or verification.get("candidate_verified") is not True
        or verification.get("independent_public_replay_performed") is not True
        or run.get("run_spec_sha256") != spec["candidate_run_spec_sha256"]
        or verification.get("run_spec_sha256") != spec["candidate_run_spec_sha256"]
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "candidate receipt binding rejected"
        )
    false_fields = (
        "formal_materialization_eligible",
        "producer_binding_verified",
        "runtime_dependency_authority_verified",
        "source_authority_complete",
        "source_authority_verified",
        "verified",
    )
    _false_scope(run, false_fields, label="candidate run")
    _false_scope(verification, false_fields, label="candidate verification")
    run_root = _strict_sha256(run["candidate_root_sha256"], label="candidate root")
    if verification.get("run_candidate_root_sha256") != run_root:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "candidate replay root binding rejected"
        )
    _strict_sha256(
        verification["verification_root_sha256"], label="candidate verification root"
    )
    return run, verification


def _validated_proof(spec: Mapping[str, Any], value: Any) -> dict[str, Any]:
    if value is None:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "independent authority proof required"
        )
    if type(value) is not dict or set(value) != _PROOF_FIELDS:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "authority proof fields rejected"
        )
    proof = dict(value)
    missing = [field for field in AUTHORITY_GATE_FIELDS if proof.get(field) is not True]
    if missing:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            f"authority gate rejected: {missing[0]}"
        )
    for field in _PROOF_ROOT_FIELDS:
        _strict_sha256(proof[field], label=field)
    semantic = spec["semantic_input"]
    for field in (
        "input_closure_root_sha256",
        "producer_source_authority_sha256",
        "runtime_authority_root_sha256",
    ):
        if proof[field] != semantic[field]:
            raise FactorV3ParentSourceDevelopmentAuthorityError(
                f"authority proof {field} binding rejected"
            )
    return proof


def build_factor_v3_parent_source_development_authority_spec(
    *,
    semantic_input: Mapping[str, Any],
    candidate_run_spec_sha256: str,
    run_root: str | Path,
    verification_root: str | Path,
    global_attempt_ledger_root: str | Path,
) -> dict[str, Any]:
    """Build a path-sensitive candidate envelope with every authority bit false."""

    semantic = _validated_semantic_input(dict(semantic_input))
    candidate_sha = _strict_sha256(
        candidate_run_spec_sha256, label="candidate run spec"
    )
    resolved_run_root = _absolute_path(run_root, label="run root")
    resolved_verification_root = _absolute_path(
        verification_root, label="verification root"
    )
    ledger_root = _absolute_path(
        global_attempt_ledger_root, label="global attempt ledger root"
    )
    _reject_overlapping_roots(
        resolved_run_root,
        resolved_verification_root,
        ledger_root,
    )
    semantic_root = _canonical_sha256(semantic)
    attempt_key = _canonical_sha256(
        {
            "schema": ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
        }
    )
    claim_root = ledger_root / "attempts" / "sha256" / attempt_key[:2] / attempt_key
    spec: dict[str, Any] = {
        "attempt_key_sha256": attempt_key,
        "authority_status": "CANDIDATE_AUTHORITY_CONTRACT_ONLY",
        "candidate_run_spec_sha256": candidate_sha,
        "development_only": True,
        "global_attempt_ledger_root": str(ledger_root),
        "global_run_claim_path": str(claim_root / "run.claim.json"),
        "global_verify_claim_path": str(claim_root / "verify.claim.json"),
        "run_root": str(resolved_run_root),
        "schema": RUN_SPEC_SCHEMA,
        "semantic_input": semantic,
        "semantic_input_root_sha256": semantic_root,
        "verification_root": str(resolved_verification_root),
        **{field: False for field in _SPEC_FALSE_FIELDS},
    }
    spec["run_spec_sha256"] = _canonical_sha256(spec)
    return _validated_spec(spec)


def claim_factor_v3_parent_source_development_attempt(
    spec: Mapping[str, Any],
    *,
    action: str,
    global_attempt_ledger_authority_artifact_path: str | Path | None = None,
    expected_global_attempt_ledger_authority_sha256: str | None = None,
) -> dict[str, Any]:
    """Reject claims until an immutable external ledger authority is registered."""

    validated = _validated_spec(dict(spec))
    if action not in {"run", "verify"}:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "global claim action rejected"
        )
    del validated
    del global_attempt_ledger_authority_artifact_path
    del expected_global_attempt_ledger_authority_sha256
    raise FactorV3ParentSourceDevelopmentAuthorityError(
        "registered global attempt ledger authority unavailable"
    )


def observe_factor_v3_parent_source_development_claim_pair(
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Observe caller files without granting global-attempt or source authority."""

    validated = _validated_spec(dict(spec))
    run_raw = _read_same_handle(
        Path(validated["global_run_claim_path"]),
        label="run claim",
    )
    verify_raw = _read_same_handle(
        Path(validated["global_verify_claim_path"]),
        label="verify claim",
    )
    run_claim = _validated_claim_raw(run_raw, spec=validated, action="run")
    verify_claim = _validated_claim_raw(verify_raw, spec=validated, action="verify")
    return {
        "attempt_key_sha256": validated["attempt_key_sha256"],
        "authority_status": "CANDIDATE_CLAIM_PAIR_ONLY",
        "development_only": True,
        "formal_materialization_eligible": False,
        "global_attempt_ledger_authority_verified": False,
        "global_single_attempt_verified": False,
        "parent_source_authority_verified": False,
        "run_claim_file_sha256": hashlib.sha256(run_raw).hexdigest(),
        "run_claim_sha256": run_claim["claim_sha256"],
        "same_handle_postread_verified": True,
        "schema": CLAIM_PAIR_OBSERVATION_SCHEMA,
        "semantic_input_root_sha256": validated["semantic_input_root_sha256"],
        "source_authority_complete": False,
        "source_authority_verified": False,
        "verified": False,
        "verify_claim_file_sha256": hashlib.sha256(verify_raw).hexdigest(),
        "verify_claim_sha256": verify_claim["claim_sha256"],
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }


def build_factor_v3_parent_source_development_authority_receipt(
    *,
    spec: Mapping[str, Any],
    candidate_run_result: Mapping[str, Any],
    candidate_verification_result: Mapping[str, Any],
    proof: Mapping[str, Any] | None = None,
    native_tcb_authority_artifact_path: str | Path | None = None,
    expected_native_tcb_authority_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """Reserved authority gate; no receipt can be emitted without registered TCB."""

    validated = _validated_spec(dict(spec))
    run_result, verification_result = _validated_candidate_results(
        validated,
        candidate_run_result,
        candidate_verification_result,
    )
    del run_result, verification_result
    if proof is not None:
        _validated_proof(validated, proof)
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "caller authority proof rejected"
        )
    if (
        native_tcb_authority_artifact_path is None
        and expected_native_tcb_authority_artifact_sha256 is None
    ):
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "independent native TCB authority proof required"
        )
    if REGISTERED_NATIVE_TCB_AUTHORITY_ARTIFACT_SHA256 is None:
        raise FactorV3ParentSourceDevelopmentAuthorityError(
            "registered native TCB authority artifact unavailable"
        )
    raise FactorV3ParentSourceDevelopmentAuthorityError(
        "registered native TCB authority artifact validation unavailable"
    )
