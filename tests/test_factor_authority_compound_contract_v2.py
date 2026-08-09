from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app import factor_authority_compound_contract_v2 as contract
from app import factor_v2_decision_branch_selector as branch_selector
from app import factor_v3_formal_trusted_supervisor as root_lease


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write_cas(
    root: Path,
    *,
    role: str,
    category: str,
    commit_order: int,
    attempt_key_sha256: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": f"{role}-fixture/v1",
        "category": category,
        "commit_order": commit_order,
        "attempt_key_sha256": attempt_key_sha256 or _sha("attempt"),
        "payload_sha256": _sha(f"payload:{role}"),
    }
    self_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    artifact = {**payload, "artifact_sha256": self_sha256}
    raw = _canonical_bytes(artifact)
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    path = root / "sha256" / raw_sha256[:2] / f"{raw_sha256}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "schema": contract.COMPOUND_CAS_DESCRIPTOR_SCHEMA,
        "role": role,
        "category": category,
        "path": str(path.resolve()),
        "raw_sha256": raw_sha256,
        "self_sha256": self_sha256,
        "commit_order": commit_order,
        "attempt_key_sha256": artifact["attempt_key_sha256"],
    }


def _four_cas(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return {
        "parent_producer": _write_cas(
            tmp_path / "parent-producer",
            role="parent_producer",
            category="factor-v3-parent-source-producer",
            commit_order=1,
        ),
        "parent_verifier": _write_cas(
            tmp_path / "parent-verifier",
            role="parent_verifier",
            category="factor-v3-parent-source-independent-verifier",
            commit_order=5,
        ),
        "evaluator_producer": _write_cas(
            tmp_path / "evaluator-producer",
            role="evaluator_producer",
            category="factor-v2-terminal-evaluator-producer",
            commit_order=2,
        ),
        "evaluator_verifier": _write_cas(
            tmp_path / "evaluator-verifier",
            role="evaluator_verifier",
            category="factor-v2-terminal-evaluator-independent-verifier",
            commit_order=6,
        ),
    }


def _identity_fixture() -> dict[str, Any]:
    semantic = {
        "schema": contract.COMPOUND_SEMANTIC_IDENTITY_SCHEMA,
        "parent_producer_root_sha256": _sha("parent-producer"),
        "parent_verifier_root_sha256": _sha("parent-verifier"),
        "evaluator_producer_root_sha256": _sha("evaluator-producer"),
        "evaluator_verifier_root_sha256": _sha("evaluator-verifier"),
    }
    semantic_root = hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    attempt = root_lease.factor_v3_parent_source_derive_attempt_key_sha256(
        semantic_root
    )
    global_identity = (
        root_lease.factor_v3_parent_source_derive_global_attempt_identity_sha256(
            attempt
        )
    )
    return {
        "semantic_identity": semantic,
        "semantic_input_root_sha256": semantic_root,
        "attempt_key_sha256": attempt,
        "global_attempt_identity_sha256": global_identity,
    }


def _run_spec(tmp_path: Path) -> dict[str, Any]:
    return {
        "schema": contract.COMPOUND_RUN_SPEC_SCHEMA,
        "identity_binding": _identity_fixture(),
        "authority_cas": _four_cas(tmp_path),
        "global_attempt_ledger_root": str((tmp_path / "global-ledger").resolve()),
    }


def test_registry_none_rejects_before_any_cas_write(tmp_path: Path) -> None:
    assert (
        contract.REGISTERED_COMPOUND_NATIVE_TCB_SHA256,
        contract.REGISTERED_PARENT_PRODUCER_AUTHORITY_SHA256,
        contract.REGISTERED_PARENT_VERIFIER_AUTHORITY_SHA256,
        contract.REGISTERED_EVALUATOR_PRODUCER_AUTHORITY_SHA256,
        contract.REGISTERED_EVALUATOR_VERIFIER_AUTHORITY_SHA256,
    ) == (None, None, None, None, None)
    output_root = tmp_path / "compound-cas"
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="registered compound authority",
    ):
        contract.publish_compound_authority(
            output_root=output_root,
            run_spec={},
            native_result={"verified": True},
        )
    assert not output_root.exists()


def test_semantic_attempt_global_identity_is_path_independent(
    tmp_path: Path,
) -> None:
    semantic_payload = {
        "schema": contract.COMPOUND_SEMANTIC_IDENTITY_SCHEMA,
        "parent_producer_root_sha256": _sha("parent-producer"),
        "parent_verifier_root_sha256": _sha("parent-verifier"),
        "evaluator_producer_root_sha256": _sha("evaluator-producer"),
        "evaluator_verifier_root_sha256": _sha("evaluator-verifier"),
    }
    semantic_root = hashlib.sha256(_canonical_bytes(semantic_payload)).hexdigest()
    expected_attempt = root_lease.factor_v3_parent_source_derive_attempt_key_sha256(
        semantic_root
    )
    expected_global = (
        root_lease.factor_v3_parent_source_derive_global_attempt_identity_sha256(
            expected_attempt
        )
    )
    left = contract.build_compound_identity_binding(
        parent_producer_root_sha256=_sha("parent-producer"),
        parent_verifier_root_sha256=_sha("parent-verifier"),
        evaluator_producer_root_sha256=_sha("evaluator-producer"),
        evaluator_verifier_root_sha256=_sha("evaluator-verifier"),
    )
    (tmp_path / "left").mkdir()
    right = contract.build_compound_identity_binding(
        parent_producer_root_sha256=_sha("parent-producer"),
        parent_verifier_root_sha256=_sha("parent-verifier"),
        evaluator_producer_root_sha256=_sha("evaluator-producer"),
        evaluator_verifier_root_sha256=_sha("evaluator-verifier"),
    )
    (tmp_path / "right").mkdir()
    assert left == right
    assert left["semantic_input_root_sha256"] == semantic_root
    assert left["attempt_key_sha256"] == expected_attempt
    assert left["global_attempt_identity_sha256"] == expected_global
    assert not any("path" in key or "root_path" in key for key in left)


def test_run_spec_binds_four_disjoint_parent_evaluator_cas_roots(
    tmp_path: Path,
) -> None:
    cas = _four_cas(tmp_path)
    spec = contract.build_compound_run_spec(
        identity_binding=_identity_fixture(),
        authority_cas=cas,
        global_attempt_ledger_root=tmp_path / "global-ledger",
    )
    assert spec["schema"] == contract.COMPOUND_RUN_SPEC_SCHEMA
    assert tuple(spec["authority_cas"]) == contract.AUTHORITY_CAS_ROLES
    assert spec["authority_cas"] == cas
    roots = [Path(cas[role]["path"]).parents[2] for role in contract.AUTHORITY_CAS_ROLES]
    assert all(
        not left.is_relative_to(right) and not right.is_relative_to(left)
        for index, left in enumerate(roots)
        for right in roots[index + 1 :]
    )


def test_terminal_epoch_without_all_physical_cas_is_rejected(
    tmp_path: Path,
) -> None:
    missing = {
        role: {
            "schema": contract.COMPOUND_CAS_DESCRIPTOR_SCHEMA,
            "path": str((tmp_path / role / "missing.json").resolve()),
            "raw_sha256": _sha(f"raw:{role}"),
            "self_sha256": _sha(f"self:{role}"),
            "category": role,
        }
        for role in contract.AUTHORITY_CAS_ROLES
    }
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="terminal epoch.*CAS closure",
    ):
        contract.validate_compound_terminal_epoch(
            run_spec={"authority_cas": missing},
            epoch_observation={"state": "TERMINAL", "epoch": 4},
        )


def test_run_receipt_is_after_both_producers_and_native_completion(
    tmp_path: Path,
) -> None:
    cas = _four_cas(tmp_path)
    native_completion = {
        "category": "native-run-completion",
        "commit_order": 3,
        "raw_sha256": _sha("native-run-completion"),
    }
    receipt = contract.publish_compound_run_receipt(
        destination=tmp_path / "epoch" / "run.receipt.json",
        run_spec=_run_spec(tmp_path / "spec"),
        parent_producer_cas=cas["parent_producer"],
        evaluator_producer_cas=cas["evaluator_producer"],
        native_run_completion=native_completion,
    )
    assert receipt["schema"] == contract.COMPOUND_RUN_RECEIPT_SCHEMA
    assert receipt["commit_order"] > max(1, 2, 3)
    assert receipt["published_after_sha256"] == [
        cas["parent_producer"]["raw_sha256"],
        cas["evaluator_producer"]["raw_sha256"],
        native_completion["raw_sha256"],
    ]


def test_terminal_receipt_is_after_both_verifiers_and_verify_completion(
    tmp_path: Path,
) -> None:
    cas = _four_cas(tmp_path)
    run_receipt = {
        "schema": contract.COMPOUND_RUN_RECEIPT_SCHEMA,
        "commit_order": 4,
        "receipt_sha256": _sha("run-receipt"),
    }
    native_completion = {
        "category": "native-verify-completion",
        "commit_order": 7,
        "raw_sha256": _sha("native-verify-completion"),
    }
    receipt = contract.publish_compound_terminal_receipt(
        destination=tmp_path / "epoch" / "terminal.receipt.json",
        run_receipt=run_receipt,
        parent_verifier_cas=cas["parent_verifier"],
        evaluator_verifier_cas=cas["evaluator_verifier"],
        native_verify_completion=native_completion,
    )
    assert receipt["schema"] == contract.COMPOUND_TERMINAL_RECEIPT_SCHEMA
    assert receipt["commit_order"] > max(4, 5, 6, 7)
    assert receipt["published_after_sha256"][-1] == native_completion["raw_sha256"]


def test_physical_cas_binds_path_raw_self_hash_and_category(
    tmp_path: Path,
) -> None:
    descriptor = _write_cas(
        tmp_path / "cas",
        role="parent_producer",
        category="factor-v3-parent-source-producer",
        commit_order=1,
    )
    validated = contract.validate_physical_cas_artifact(
        descriptor=descriptor,
        expected_category="factor-v3-parent-source-producer",
        allowed_root=tmp_path / "cas",
    )
    assert validated == descriptor
    assert Path(validated["path"]).read_bytes()


def test_parent_and_evaluator_share_exact_compound_attempt(tmp_path: Path) -> None:
    spec = _run_spec(tmp_path)
    attempt = spec["identity_binding"]["attempt_key_sha256"]
    result = contract.validate_shared_compound_attempt(
        run_spec=spec,
        parent_evidence={"attempt_key_sha256": attempt},
        evaluator_evidence={"attempt_key_sha256": attempt},
    )
    assert result["shared_compound_attempt_verified"] is True
    assert result["attempt_key_sha256"] == attempt


def test_caller_all_true_and_native_mapping_cannot_grant_authority(
    tmp_path: Path,
) -> None:
    caller_claims = {
        **{field: True for field in contract.AUTHORITY_TRUE_FIELDS},
        "native_runtime_authority_verified": True,
    }
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="caller.*native.*authority",
    ):
        contract.authorize_compound_authority(
            run_spec=_run_spec(tmp_path),
            caller_claims=caller_claims,
            native_result={"verified": True, "authority_sha256": _sha("caller")},
        )


def test_disposable_observation_keeps_every_authority_and_safety_gate_false(
    tmp_path: Path,
) -> None:
    observation = contract.build_disposable_compound_observation(
        run_spec=_run_spec(tmp_path),
        observed_evidence=[],
    )
    assert observation["schema"] == contract.COMPOUND_DISPOSABLE_OBSERVATION_SCHEMA
    assert observation["test_fixture_only"] is True
    assert observation["disposable"] is True
    assert all(observation[field] is False for field in contract.AUTHORITY_TRUE_FIELDS)
    assert all(observation[field] is False for field in contract.SAFETY_FALSE_FIELDS)


def test_low_rvol_selection_without_dedicated_authority_is_rejected(
    tmp_path: Path,
) -> None:
    evaluator = _write_cas(
        tmp_path / "evaluator",
        role="evaluator_producer",
        category="factor-v2-terminal-evaluator-producer",
        commit_order=2,
    )
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="low-rvol.*authority",
    ):
        contract.require_factor_v2_low_rvol_authority(
            evaluator_cas={
                **evaluator,
                "selected_branch": branch_selector.LOW_RVOL_BRANCH,
            },
            low_rvol_authority_cas=None,
        )


def test_publication_is_last_and_failure_leaves_no_success_artifact(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "compound-output"
    with pytest.raises(
        contract.FactorAuthorityCompoundPublicationFailure,
        match="injected before success publication",
    ):
        contract.publish_compound_authority(
            output_root=output_root,
            run_spec=_run_spec(tmp_path / "spec"),
            native_result={"verified": False, "test_fixture_only": True},
            test_fixture_only=True,
            _test_failure_stage="before_success_publication",
        )
    success_names = {"publication.json", "receipt.json", "success.json"}
    assert not output_root.exists() or not any(
        path.name in success_names for path in output_root.rglob("*")
    )
