from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import factor_v3_parent_source_authority_runner as candidate
from app import factor_v3_parent_source_development_authority as authority


def _sha(character: str) -> str:
    return character * 64


def _oid(character: str) -> str:
    return character * 40


def _semantic_input() -> dict[str, object]:
    return {
        "candidate_contract_root_sha256": _sha("1"),
        "control_contract_root_sha256": _sha("2"),
        "frozen_source_blob_root_sha256": _sha("3"),
        "frozen_source_commit": _oid("4"),
        "frozen_source_tree_oid": _oid("5"),
        "input_closure_root_sha256": _sha("6"),
        "overlay_artifact_sha256": _sha("7"),
        "parent_artifact_sha256": _sha("8"),
        "producer_source_authority_sha256": _sha("9"),
        "runtime_authority_root_sha256": _sha("a"),
        "schema": authority.SEMANTIC_INPUT_SCHEMA,
        "suspension_bundle_sha256": _sha("b"),
    }


def _spec(
    tmp_path: Path,
    *,
    run_name: str = "run-a",
    verification_name: str = "verify-a",
) -> dict[str, object]:
    return authority.build_factor_v3_parent_source_development_authority_spec(
        semantic_input=_semantic_input(),
        candidate_run_spec_sha256=_sha("c"),
        run_root=tmp_path / run_name,
        verification_root=tmp_path / verification_name,
        global_attempt_ledger_root=tmp_path / "global-ledger",
    )


def _candidate_run_result(spec: dict[str, object]) -> dict[str, object]:
    return {
        "candidate_root_sha256": _sha("d"),
        "candidate_verified": True,
        "formal_materialization_eligible": False,
        "producer_binding_verified": False,
        "run_spec_sha256": spec["candidate_run_spec_sha256"],
        "runtime_dependency_authority_verified": False,
        "schema": candidate.RESULT_SCHEMA,
        "source_authority_complete": False,
        "source_authority_verified": False,
        "verified": False,
    }


def _candidate_verification_result(spec: dict[str, object]) -> dict[str, object]:
    return {
        "candidate_verified": True,
        "formal_materialization_eligible": False,
        "independent_public_replay_performed": True,
        "producer_binding_verified": False,
        "run_candidate_root_sha256": _sha("d"),
        "run_spec_sha256": spec["candidate_run_spec_sha256"],
        "runtime_dependency_authority_verified": False,
        "schema": candidate.VERIFICATION_RESULT_SCHEMA,
        "source_authority_complete": False,
        "source_authority_verified": False,
        "verification_root_sha256": _sha("e"),
        "verified": False,
    }


def _complete_proof() -> dict[str, object]:
    return {
        **{field: True for field in authority.AUTHORITY_GATE_FIELDS},
        "global_run_claim_sha256": _sha("f"),
        "global_verify_claim_sha256": _sha("0"),
        "input_closure_root_sha256": _sha("6"),
        "loaded_source_ledger_root_sha256": _sha("1"),
        "producer_source_authority_sha256": _sha("9"),
        "runtime_authority_root_sha256": _sha("a"),
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _claim_bytes(
    spec: dict[str, object],
    *,
    action: str,
    attempt_key_sha256: str | None = None,
) -> tuple[dict[str, object], bytes]:
    claim: dict[str, object] = {
        "action": action,
        "attempt_key_sha256": attempt_key_sha256 or spec["attempt_key_sha256"],
        "development_only": True,
        "formal_materialization_eligible": False,
        "parent_source_authority_verified": False,
        "run_spec_sha256": spec["run_spec_sha256"],
        "schema": (
            authority.RUN_CLAIM_SCHEMA
            if action == "run"
            else authority.VERIFICATION_CLAIM_SCHEMA
        ),
        "semantic_input_root_sha256": spec["semantic_input_root_sha256"],
        "single_attempt": True,
        "source_authority_complete": False,
        "source_authority_verified": False,
        "verified": False,
        **{field: False for field in authority.SAFETY_FALSE_FIELDS},
    }
    claim["claim_sha256"] = hashlib.sha256(_canonical_bytes(claim)).hexdigest()
    return claim, _canonical_bytes(claim)


def _write_claim_pair(spec: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    run_claim, run_raw = _claim_bytes(spec, action="run")
    verify_claim, verify_raw = _claim_bytes(spec, action="verify")
    run_path = Path(spec["global_run_claim_path"])
    verify_path = Path(spec["global_verify_claim_path"])
    run_path.parent.mkdir(parents=True)
    run_path.write_bytes(run_raw)
    verify_path.write_bytes(verify_raw)
    return run_claim, verify_claim


def test_candidate_receipts_alone_never_satisfy_development_authority(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="proof",
    ):
        authority.build_factor_v3_parent_source_development_authority_receipt(
            spec=spec,
            candidate_run_result=_candidate_run_result(spec),
            candidate_verification_result=_candidate_verification_result(spec),
        )


def test_semantic_attempt_key_ignores_output_paths(tmp_path: Path) -> None:
    first = _spec(tmp_path, run_name="run-a", verification_name="verify-a")
    second = _spec(tmp_path, run_name="run-b", verification_name="verify-b")

    assert first["run_spec_sha256"] != second["run_spec_sha256"]
    assert first["semantic_input_root_sha256"] == second["semantic_input_root_sha256"]
    assert first["attempt_key_sha256"] == second["attempt_key_sha256"]
    assert first["global_run_claim_path"] == second["global_run_claim_path"]
    assert first["global_verify_claim_path"] == second["global_verify_claim_path"]
    assert first["authority_status"] == "CANDIDATE_AUTHORITY_CONTRACT_ONLY"
    assert first["global_attempt_ledger_authority_verified"] is False
    assert first["parent_source_authority_verified"] is False
    assert first["source_authority_complete"] is False
    assert first["source_authority_verified"] is False
    assert first["formal_materialization_eligible"] is False


def test_candidate_contract_cannot_claim_caller_selected_ledger_root(
    tmp_path: Path,
) -> None:
    first = _spec(tmp_path, run_name="run-a", verification_name="verify-a")
    second = _spec(tmp_path, run_name="run-b", verification_name="verify-b")

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="registered global attempt ledger authority",
    ):
        authority.claim_factor_v3_parent_source_development_attempt(first, action="run")
    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="registered global attempt ledger authority",
    ):
        authority.claim_factor_v3_parent_source_development_attempt(second, action="run")
    assert not Path(first["global_run_claim_path"]).exists()


def test_claim_pair_observation_reopens_canonical_bytes_without_elevation(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    run_claim, verify_claim = _write_claim_pair(spec)

    observation = authority.observe_factor_v3_parent_source_development_claim_pair(
        spec
    )

    assert observation["schema"] == authority.CLAIM_PAIR_OBSERVATION_SCHEMA
    assert observation["authority_status"] == "CANDIDATE_CLAIM_PAIR_ONLY"
    assert observation["attempt_key_sha256"] == spec["attempt_key_sha256"]
    assert observation["run_claim_sha256"] == run_claim["claim_sha256"]
    assert observation["verify_claim_sha256"] == verify_claim["claim_sha256"]
    assert observation["same_handle_postread_verified"] is True
    assert observation["global_single_attempt_verified"] is False
    assert observation["verified"] is False
    assert observation["global_attempt_ledger_authority_verified"] is False
    assert observation["parent_source_authority_verified"] is False
    assert observation["source_authority_complete"] is False
    assert observation["source_authority_verified"] is False
    assert observation["formal_materialization_eligible"] is False


def test_claim_pair_observation_rejects_noncanonical_or_wrong_identity(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    _write_claim_pair(spec)
    run_path = Path(spec["global_run_claim_path"])
    run_path.write_bytes(run_path.read_bytes() + b"\n")

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="canonical",
    ):
        authority.observe_factor_v3_parent_source_development_claim_pair(spec)

    run_claim, run_raw = _claim_bytes(spec, action="run")
    assert run_claim["claim_sha256"]
    run_path.write_bytes(run_raw)
    _verify_claim, verify_raw = _claim_bytes(
        spec,
        action="verify",
        attempt_key_sha256=_sha("f"),
    )
    Path(spec["global_verify_claim_path"]).write_bytes(verify_raw)
    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="binding",
    ):
        authority.observe_factor_v3_parent_source_development_claim_pair(spec)


@pytest.mark.parametrize(
    ("run_name", "verification_name", "ledger_name"),
    (
        ("output", "output/verify", "ledger"),
        ("output/run", "output", "ledger"),
        ("ledger/run", "verify", "ledger"),
    ),
)
def test_output_and_ledger_roots_must_not_overlap(
    tmp_path: Path,
    run_name: str,
    verification_name: str,
    ledger_name: str,
) -> None:
    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="overlap",
    ):
        authority.build_factor_v3_parent_source_development_authority_spec(
            semantic_input=_semantic_input(),
            candidate_run_spec_sha256=_sha("c"),
            run_root=tmp_path / run_name,
            verification_root=tmp_path / verification_name,
            global_attempt_ledger_root=tmp_path / ledger_name,
        )


@pytest.mark.parametrize("missing_gate", authority.AUTHORITY_GATE_FIELDS)
def test_all_gates_are_required_for_authority(
    tmp_path: Path,
    missing_gate: str,
) -> None:
    spec = _spec(tmp_path)
    proof = _complete_proof()
    proof[missing_gate] = False

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="gate",
    ):
        authority.build_factor_v3_parent_source_development_authority_receipt(
            spec=spec,
            candidate_run_result=_candidate_run_result(spec),
            candidate_verification_result=_candidate_verification_result(spec),
            proof=proof,
        )


def test_caller_all_true_proof_never_self_signs_authority(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="caller authority proof",
    ):
        authority.build_factor_v3_parent_source_development_authority_receipt(
            spec=spec,
            candidate_run_result=_candidate_run_result(spec),
            candidate_verification_result=_candidate_verification_result(spec),
            proof=_complete_proof(),
        )


def test_candidate_scope_cannot_hide_true_safety_field(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    run_result = _candidate_run_result(spec)
    run_result["automatic_trading_eligible"] = True

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="candidate run false scope",
    ):
        authority.build_factor_v3_parent_source_development_authority_receipt(
            spec=spec,
            candidate_run_result=run_result,
            candidate_verification_result=_candidate_verification_result(spec),
        )


def test_unregistered_self_reported_native_artifact_never_elevates(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    artifact = {
        "authority_status": "DEVELOPMENT_PARENT_SOURCE_AUTHORITY_VERIFIED",
        "independent_verifier_verified": True,
        "native_tcb_verified": True,
        "schema": authority.NATIVE_TCB_AUTHORITY_ARTIFACT_SCHEMA,
        **_complete_proof(),
    }
    raw = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path = tmp_path / "caller-native-authority.json"
    path.write_bytes(raw)
    caller_sha256 = hashlib.sha256(raw).hexdigest()

    with pytest.raises(
        authority.FactorV3ParentSourceDevelopmentAuthorityError,
        match="registered native TCB authority artifact unavailable",
    ):
        authority.build_factor_v3_parent_source_development_authority_receipt(
            spec=spec,
            candidate_run_result=_candidate_run_result(spec),
            candidate_verification_result=_candidate_verification_result(spec),
            native_tcb_authority_artifact_path=path,
            expected_native_tcb_authority_artifact_sha256=caller_sha256,
        )


def test_old_candidate_contract_remains_explicitly_false() -> None:
    result = candidate._result(
        {"run_spec_sha256": _sha("c")},
        {
            "file_sha256": _sha("d"),
            "relative_path": "receipts/sha256/dd/example.json",
            "root_sha256": _sha("e"),
        },
    )

    assert result["schema"] == candidate.RESULT_SCHEMA
    assert result["authority_status"] == "CANDIDATE_PARENT_REPLAY_ONLY"
    assert result["candidate_verified"] is True
    assert result["formal_materialization_eligible"] is False
    assert result["source_authority_verified"] is False
    assert result["verified"] is False
    assert all(result[field] is False for field in candidate.SAFETY_FALSE_FIELDS)
