from __future__ import annotations

# ruff: noqa: E731

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from app import factor_authority_compound_contract_v2 as contract
from app import factor_authority_compound_native_client as native_client


PROGRAM_SET_ROOT_SHA256 = hashlib.sha256(b"pre-existing-program-set").hexdigest()
PARENT_SEMANTIC_ROOT_SHA256 = hashlib.sha256(b"parent-semantic-input").hexdigest()
EVALUATOR_SEMANTIC_ROOT_SHA256 = hashlib.sha256(
    b"evaluator-semantic-input"
).hexdigest()
DECISION_AUTHORITY_ROOT_SHA256 = hashlib.sha256(
    b"pre-existing-factor-v2-terminal-decision"
).hexdigest()
LOW_RVOL_AUTHORITY_ROOT_SHA256 = hashlib.sha256(
    b"pre-existing-factor-v2-low-rvol-branch"
).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _expected_identity() -> dict[str, Any]:
    semantic = {
        "schema": contract.COMPOUND_SEMANTIC_IDENTITY_SCHEMA,
        "program_set_root_sha256": PROGRAM_SET_ROOT_SHA256,
        "parent_semantic_input_root_sha256": PARENT_SEMANTIC_ROOT_SHA256,
        "evaluator_semantic_input_root_sha256": EVALUATOR_SEMANTIC_ROOT_SHA256,
        "factor_v2_terminal_decision_authority_root_sha256": (
            DECISION_AUTHORITY_ROOT_SHA256
        ),
        "factor_v2_low_rvol_branch_authority_root_sha256": (
            LOW_RVOL_AUTHORITY_ROOT_SHA256
        ),
    }
    semantic_root = hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    attempt = hashlib.sha256(
        _canonical_bytes(
            {
                "schema": (
                    "factor-v3-parent-source-development-authority-attempt-key/v1"
                ),
                "semantic_input_root_sha256": semantic_root,
            }
        )
    ).hexdigest()
    return {
        "semantic_identity": semantic,
        "semantic_input_root_sha256": semantic_root,
        "attempt_key_sha256": attempt,
        "global_attempt_identity_sha256": (
            hashlib.sha256(
                _canonical_bytes(
                    {
                        "attempt_key_sha256": attempt,
                        "schema": "factor-v3-parent-source-global-attempt-identity/v1",
                    }
                )
            ).hexdigest()
        ),
    }


@pytest.mark.parametrize("scenario", ["identity", "formal-signatures"])
def test_identity_precedes_outputs_and_formal_api_has_no_authority_override(
    scenario: str,
) -> None:
    formal_functions = (
        contract.open_registered_compound_deployment_policy_authority,
        contract.build_compound_run_spec,
        contract.open_held_compound_cas,
        contract.start_compound_run_with_low_rvol_gate,
        contract.publish_compound_run_receipt,
        contract.publish_compound_terminal_receipt,
        contract.authorize_compound_authority,
        contract.publish_compound_authority,
    )
    forbidden = {"test_fixture_only", "caller_claims", "commit_order", "ctime"}
    for function in formal_functions:
        assert not forbidden & set(inspect.signature(function).parameters)
    for function in formal_functions[2:]:
        hints = get_type_hints(function)
        if "run_spec" in hints:
            assert hints["run_spec"] is native_client.HeldCompoundRunSpec
        if "root_lease_capability" in hints:
            assert (
                hints["root_lease_capability"]
                is native_client.HeldCompoundRootLease
            )
    contract_source = inspect.getsource(contract)
    assert "_ParentSourceRootLease" not in contract_source
    assert "factor_v3_formal_trusted_supervisor" not in contract_source
    assert contract.COMPOUND_NATIVE_ROOT_LEASE_BINDING["capability"] == (
        "HeldCompoundRootLease"
    )
    if scenario == "formal-signatures":
        with pytest.raises(
            contract.FactorAuthorityCompoundContractV2Error,
            match="opaque|native|policy|run spec",
        ):
            contract.build_compound_run_spec(
                native_session_set={},  # type: ignore[arg-type]
                identity_binding=_expected_identity(),
                deployment_policy_authority={},  # type: ignore[arg-type]
            )
        return
    identity = contract.build_compound_identity_binding(
        program_set_root_sha256=PROGRAM_SET_ROOT_SHA256,
        parent_semantic_input_root_sha256=PARENT_SEMANTIC_ROOT_SHA256,
        evaluator_semantic_input_root_sha256=EVALUATOR_SEMANTIC_ROOT_SHA256,
        factor_v2_terminal_decision_authority_root_sha256=(
            DECISION_AUTHORITY_ROOT_SHA256
        ),
        factor_v2_low_rvol_branch_authority_root_sha256=(
            LOW_RVOL_AUTHORITY_ROOT_SHA256
        ),
    )
    assert identity == _expected_identity()
    serialized = json.dumps(identity, sort_keys=True)
    assert not any(
        word in serialized
        for word in ("path", "output", "receipt", "artifact", "namespace")
    )


def test_production_policy_and_native_registries_reject_before_any_write(
    tmp_path: Path,
) -> None:
    assert contract.REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH is None
    assert contract.REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_RAW_SHA256 is None
    assert contract.HANDOFF_READY == 0
    assert native_client.REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256 is None
    assert native_client.REGISTERED_COMPOUND_NATIVE_BROKER_PATH is None
    assert native_client.REGISTERED_COMPOUND_NATIVE_BROKER_FILE_SHA256 is None
    assert native_client.HANDOFF_READY == 0
    output_root = tmp_path / "must-remain-absent"
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="registered|policy|native|session",
    ):
        contract.open_registered_compound_deployment_policy_authority(
            native_session_set={},  # type: ignore[arg-type]
        )
    assert not output_root.exists()


@pytest.mark.parametrize("phase", ["run", "verify", "terminal"])
def test_native_and_receipt_raw_closures_are_exact_and_have_no_time_order_fields(
    phase: str,
) -> None:
    assert contract.RUN_RECEIPT_RAW_BINDINGS == (
        "root_run_claim_raw_sha256",
        "parent_producer_raw_sha256",
        "evaluator_producer_raw_sha256",
        "native_run_completion_raw_sha256",
    )
    assert contract.TERMINAL_RECEIPT_RAW_BINDINGS == (
        "compound_run_receipt_raw_sha256",
        "root_run_receipt_raw_sha256",
        "root_verify_claim_raw_sha256",
        "parent_verifier_raw_sha256",
        "evaluator_verifier_raw_sha256",
        "native_verify_completion_raw_sha256",
    )
    assert "ctime" not in contract.NATIVE_TERMINAL_RAW_BINDINGS
    assert "commit_order" not in contract.NATIVE_TERMINAL_RAW_BINDINGS
    plain: dict[str, Any] = {}
    if phase == "run":
        call = lambda: contract.validate_native_run_exact_closure(
            native_session_set=plain,  # type: ignore[arg-type]
            root_lease_capability=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            parent_producer_cas=plain,  # type: ignore[arg-type]
            evaluator_producer_cas=plain,  # type: ignore[arg-type]
            parent_producer_production=plain,  # type: ignore[arg-type]
            evaluator_producer_production=plain,  # type: ignore[arg-type]
            native_run_completion=plain,  # type: ignore[arg-type]
        )
    elif phase == "verify":
        call = lambda: contract.validate_native_verify_exact_closure(
            native_session_set=plain,  # type: ignore[arg-type]
            root_lease_capability=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            compound_run_receipt_cas=plain,  # type: ignore[arg-type]
            parent_verifier_cas=plain,  # type: ignore[arg-type]
            evaluator_verifier_cas=plain,  # type: ignore[arg-type]
            parent_verifier_production=plain,  # type: ignore[arg-type]
            evaluator_verifier_production=plain,  # type: ignore[arg-type]
            native_verify_completion=plain,  # type: ignore[arg-type]
        )
    else:
        call = lambda: contract.validate_native_terminal_exact_closure(
            native_session_set=plain,  # type: ignore[arg-type]
            root_lease_capability=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            role_artifacts=plain,  # type: ignore[arg-type]
            role_productions=plain,  # type: ignore[arg-type]
            compound_run_receipt_cas=plain,  # type: ignore[arg-type]
            compound_terminal_receipt_cas=plain,  # type: ignore[arg-type]
            native_run_completion=plain,  # type: ignore[arg-type]
            native_verify_completion=plain,  # type: ignore[arg-type]
            native_terminal_authority=plain,  # type: ignore[arg-type]
        )
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="opaque native|compiled broker|exact raw closure",
    ):
        call()


@pytest.mark.parametrize(
    "entrypoint",
    ["run-spec", "held-cas", "forged-held-cas", "low-rvol", "publication"],
)
def test_plain_mapping_and_python_forgery_never_enter_formal_chain(
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plain: dict[str, Any] = {}
    hook_calls: list[str] = []
    monkeypatch.setattr(
        contract,
        "_publication_probe",
        lambda stage, held: hook_calls.append(stage),
    )
    if entrypoint == "run-spec":
        call = lambda: contract.build_compound_run_spec(
            native_session_set=plain,  # type: ignore[arg-type]
            identity_binding=_expected_identity(),
            deployment_policy_authority=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "held-cas":
        call = lambda: contract.open_held_compound_cas(
            native_session_set=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            namespace_name="parent_producer",
            expected_raw_sha256="0" * 64,
        )
    elif entrypoint == "forged-held-cas":
        forged = object.__new__(native_client.HeldRegisteredCas)
        call = lambda: contract.postverify_held_compound_cas(
            native_session_set=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            held_cas=forged,
        )
    elif entrypoint == "low-rvol":
        call = lambda: contract.start_compound_run_with_low_rvol_gate(
            native_session_set=plain,  # type: ignore[arg-type]
            root_lease_capability=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            factor_v2_terminal_decision_authority_cas=plain,  # type: ignore[arg-type]
            low_rvol_branch_authority_cas=None,
        )
    else:
        call = lambda: contract.publish_compound_authority(
            native_session_set=plain,  # type: ignore[arg-type]
            root_lease_capability=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            role_artifacts=plain,  # type: ignore[arg-type]
            role_productions=plain,  # type: ignore[arg-type]
            compound_run_receipt_cas=plain,  # type: ignore[arg-type]
            compound_terminal_receipt_cas=plain,  # type: ignore[arg-type]
            native_run_completion=plain,  # type: ignore[arg-type]
            native_verify_completion=plain,  # type: ignore[arg-type]
            native_terminal_authority=plain,  # type: ignore[arg-type]
        )
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="opaque|native|capability|run spec|closure",
    ):
        call()
    assert hook_calls == []
    assert not (tmp_path / "success").exists()
