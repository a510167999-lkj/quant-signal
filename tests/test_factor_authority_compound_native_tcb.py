from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from app import factor_authority_compound_contract_v2 as contract
from app import factor_authority_compound_native_client as native_client
from app import factor_v2_decision_branch_selector as branch_selector
from tests.test_factor_authority_compound_native_client import (
    NATIVE_PURPOSES,
    _compile_disposable_broker,
    _sha256_file,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _owner_dacl_sha256(path: Path) -> str:
    completed = subprocess.run(
        ["icacls", str(path)],
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def _write_cas(
    root: Path,
    category: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload_root = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    artifact = {**payload, "payload_root_sha256": payload_root}
    self_sha = hashlib.sha256(_canonical_bytes(artifact)).hexdigest()
    document = {**artifact, "artifact_sha256": self_sha}
    raw = _canonical_bytes(document)
    raw_sha = hashlib.sha256(raw).hexdigest()
    path = root / category / "sha256" / raw_sha[:2] / f"{raw_sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    information = path.stat()
    return {
        "schema": contract.COMPOUND_CAS_DESCRIPTOR_SCHEMA,
        "category": category,
        "path": str(path.resolve()),
        "raw_sha256": raw_sha,
        "self_sha256": self_sha,
        "payload_root_sha256": payload_root,
        "size_bytes": len(raw),
        "file_id": [int(information.st_dev), int(information.st_ino)],
        "nlink": int(information.st_nlink),
    }


@dataclass(frozen=True)
class _PhysicalFixture:
    executable: Path
    executable_sha256: str
    manifest_path: Path
    manifest_raw_sha256: str
    policy: dict[str, Any]
    policy_authority: dict[str, Any]
    preexisting: dict[str, dict[str, Any]]


@dataclass
class _PreparedClosure:
    fixture: _PhysicalFixture
    session_set: native_client.HeldCompoundNativeSessionSet
    run_spec: native_client.HeldCompoundRunSpec
    run_spec_evidence: dict[str, Any]
    root_lease: native_client.HeldCompoundRootLease
    role_productions: dict[str, native_client.HeldRoleProduction]
    role_artifacts: dict[str, native_client.HeldRegisteredCas]
    role_evidence: dict[str, dict[str, Any]]
    compound_run_receipt: native_client.HeldRegisteredCas
    compound_run_evidence: dict[str, Any]
    compound_terminal_receipt: native_client.HeldRegisteredCas
    compound_terminal_evidence: dict[str, Any]
    native_run: native_client.HeldNativeCompletion
    native_verify: native_client.HeldNativeCompletion
    native_terminal: native_client.HeldNativeCompletion


@pytest.fixture(scope="module")
def compiled_broker(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("compound-native-broker") / "broker.exe"
    return _compile_disposable_broker(output)


def _build_physical_fixture(
    tmp_path: Path,
    executable: Path,
    *,
    policy_violation: str | None = None,
    relation_mismatch: bool = False,
) -> _PhysicalFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    protected_roots = {
        "program_set": tmp_path / "inputs" / "program-set",
        "parent_semantic": tmp_path / "inputs" / "parent-semantic",
        "evaluator_semantic": tmp_path / "inputs" / "evaluator-semantic",
        "factor_v2_terminal_decision_authority": tmp_path / "inputs" / "decision",
        "factor_v2_low_rvol_branch_authority": tmp_path / "inputs" / "low-rvol",
    }
    for root in protected_roots.values():
        root.mkdir(parents=True)
    decision_root = _sha(f"decision-root:{tmp_path}")
    low_rvol_root = _sha(f"low-rvol-root:{tmp_path}")
    branch = _write_cas(
        protected_roots["factor_v2_low_rvol_branch_authority"],
        contract.EXPECTED_CATEGORY_BY_PREEXISTING_AUTHORITY[
            "factor_v2_low_rvol_branch_authority"
        ],
        {
            "schema": "factor-v2-low-rvol-branch-authority/v2",
            "authority_root_sha256": low_rvol_root,
            "decision_authority_root_sha256": (
                _sha("wrong-decision-root") if relation_mismatch else decision_root
            ),
            "selected_branch": branch_selector.LOW_RVOL_BRANCH,
        },
    )
    decision = _write_cas(
        protected_roots["factor_v2_terminal_decision_authority"],
        contract.EXPECTED_CATEGORY_BY_PREEXISTING_AUTHORITY[
            "factor_v2_terminal_decision_authority"
        ],
        {
            "schema": "factor-v2-terminal-decision-authority/v2",
            "authority_root_sha256": decision_root,
            "selected_branch": branch_selector.LOW_RVOL_BRANCH,
            "selected_branch_authority_raw_sha256": branch["raw_sha256"],
        },
    )
    preexisting = {
        "factor_v2_terminal_decision_authority": decision,
        "factor_v2_low_rvol_branch_authority": branch,
    }
    output_roots = {
        name: tmp_path / "outputs" / f"{index:02d}-{name}"
        for index, name in enumerate(contract.DEPLOYMENT_NAMESPACE_NAMES)
    }
    for root in output_roots.values():
        root.mkdir(parents=True)
    namespaces = {
        name: {
            "role": name,
            "canonical_root": str(output_roots[name].resolve()),
            "expected_category": contract.EXPECTED_CATEGORY_BY_NAMESPACE[name],
            "registered_source_authority_sha256": _sha(f"source:{name}"),
            "measured_owner_dacl_sha256": _owner_dacl_sha256(output_roots[name]),
        }
        for name in contract.DEPLOYMENT_NAMESPACE_NAMES
    }
    if policy_violation == "overlap":
        namespaces["parent_verifier"]["canonical_root"] = namespaces[
            "parent_producer"
        ]["canonical_root"]
    elif policy_violation == "nested":
        nested = output_roots["parent_producer"] / "nested"
        nested.mkdir()
        namespaces["parent_verifier"]["canonical_root"] = str(nested.resolve())
    elif policy_violation == "input-overlap":
        namespaces["parent_producer"]["canonical_root"] = str(
            protected_roots["parent_semantic"].resolve()
        )
    elif policy_violation == "wrong-category":
        namespaces["parent_producer"]["expected_category"] = "wrong-category"
    elif policy_violation == "unknown-field":
        namespaces["parent_producer"]["unknown"] = True
    elif policy_violation == "noncanonical":
        namespaces["parent_producer"]["canonical_root"] = (
            str(output_roots["parent_producer"]) + os.sep + ".."
        )
    elif policy_violation in {"nonempty", "staging"}:
        filename = "unexpected.json" if policy_violation == "nonempty" else ".partial"
        (output_roots["parent_producer"] / filename).write_bytes(b"partial")
    elif policy_violation == "reparse":
        link = output_roots["parent_producer"]
        target = tmp_path / "junction-target"
        target.mkdir()
        link.rmdir()
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr
        namespaces["parent_producer"]["canonical_root"] = str(link)
        namespaces["parent_producer"]["measured_owner_dacl_sha256"] = (
            _owner_dacl_sha256(link)
        )
    elif policy_violation == "dacl":
        namespaces["parent_producer"]["measured_owner_dacl_sha256"] = _sha(
            "false-owner-dacl"
        )
    elif policy_violation == "source-none":
        namespaces["parent_producer"]["registered_source_authority_sha256"] = None
    policy = {
        "schema": contract.COMPOUND_DEPLOYMENT_POLICY_SCHEMA,
        "native_tcb_source_authority_sha256": _sha("registered-native-tcb"),
        "protected_input_roots": {
            name: str(root.resolve()) for name, root in protected_roots.items()
        },
        "namespaces": namespaces,
        "preexisting_authorities": {
            name: {
                **descriptor,
                "authority_name": name,
                "canonical_root": str(protected_roots[name].resolve()),
                "expected_category": (
                    contract.EXPECTED_CATEGORY_BY_PREEXISTING_AUTHORITY[name]
                ),
                "registered_source_authority_sha256": _sha(
                    f"preexisting-source:{name}"
                ),
                "measured_owner_dacl_sha256": _owner_dacl_sha256(
                    protected_roots[name]
                ),
            }
            for name, descriptor in preexisting.items()
        },
    }
    policy_raw = _canonical_bytes(policy)
    policy_sha = hashlib.sha256(policy_raw).hexdigest()
    executable_sha = _sha256_file(executable)
    signature_binding = _sha(
        f"compiled-cng-signature:{executable_sha}:{policy_sha}"
    )
    if policy_violation == "signature":
        signature_binding = _sha("wrong-signature")
    policy_authority = _write_cas(
        tmp_path / "authorities" / "deployment-policy",
        "factor-authority-compound-deployment-policy-authority",
        {
            "schema": native_client.COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_SCHEMA,
            "deployment_policy": policy,
            "deployment_policy_sha256": policy_sha,
            "signature_algorithm": "CNG-RSA-PSS-SHA256",
            "signer_executable_sha256": executable_sha,
            "broker_signature_binding_sha256": signature_binding,
        },
    )
    manifest_descriptor = _write_cas(
        tmp_path / "authorities" / "native-manifest",
        "factor-authority-compound-native-manifest",
        {
            "schema": native_client.COMPOUND_NATIVE_MANIFEST_SCHEMA,
            "broker_file_sha256": executable_sha,
            "deployment_policy_authority_path": policy_authority["path"],
            "deployment_policy_authority_raw_sha256": policy_authority["raw_sha256"],
            "expected_native_purposes": list(NATIVE_PURPOSES),
            "handoff_ready": False,
            "disposable_compiled_fixture": True,
        },
    )
    return _PhysicalFixture(
        executable=executable,
        executable_sha256=executable_sha,
        manifest_path=Path(manifest_descriptor["path"]),
        manifest_raw_sha256=manifest_descriptor["raw_sha256"],
        policy=policy,
        policy_authority=policy_authority,
        preexisting=preexisting,
    )


def _open_session_set(
    fixture: _PhysicalFixture,
) -> native_client.HeldCompoundNativeSessionSet:
    session_set = native_client._open_disposable_test_compound_native_session_set(
        executable=fixture.executable,
        expected_executable_sha256=fixture.executable_sha256,
        fixture_manifest_authority_path=fixture.manifest_path,
        expected_fixture_manifest_raw_sha256=fixture.manifest_raw_sha256,
    )
    evidence = native_client.postverify_distinct_native_jobs(session_set=session_set)
    assert tuple(evidence["purposes"]) == NATIVE_PURPOSES
    assert len(set(evidence["process_ids"])) == 7
    assert len(set(evidence["job_identity_sha256"])) == 7
    assert all(evidence["process_live"])
    assert all(evidence["process_handles_retained"])
    assert all(evidence["job_handles_retained"])
    return session_set


def _open_run_spec(
    fixture: _PhysicalFixture,
) -> tuple[
    native_client.HeldCompoundNativeSessionSet,
    native_client.HeldCompoundRunSpec,
    dict[str, Any],
]:
    session_set = _open_session_set(fixture)
    policy_authority = contract.open_registered_compound_deployment_policy_authority(
        native_session_set=session_set
    )
    decision_payload = json.loads(
        Path(fixture.preexisting["factor_v2_terminal_decision_authority"]["path"])
        .read_bytes()
    )
    branch_payload = json.loads(
        Path(fixture.preexisting["factor_v2_low_rvol_branch_authority"]["path"])
        .read_bytes()
    )
    identity = contract.build_compound_identity_binding(
        program_set_root_sha256=_sha(f"program-set:{fixture.manifest_raw_sha256}"),
        parent_semantic_input_root_sha256=_sha("parent-semantic-input"),
        evaluator_semantic_input_root_sha256=_sha("evaluator-semantic-input"),
        factor_v2_terminal_decision_authority_root_sha256=decision_payload[
            "authority_root_sha256"
        ],
        factor_v2_low_rvol_branch_authority_root_sha256=branch_payload[
            "authority_root_sha256"
        ],
    )
    run_spec = contract.build_compound_run_spec(
        native_session_set=session_set,
        identity_binding=identity,
        deployment_policy_authority=policy_authority,
    )
    evidence = native_client.postverify_compound_run_spec(
        session_set=session_set,
        run_spec=run_spec,
    )
    assert evidence["deployment_policy_authority_raw_sha256"] == (
        fixture.policy_authority["raw_sha256"]
    )
    assert evidence["run_spec_raw_sha256"]
    return session_set, run_spec, evidence


def _hold_preexisting(
    fixture: _PhysicalFixture,
    session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    name: str,
) -> native_client.HeldRegisteredCas:
    descriptor = fixture.preexisting[name]
    return contract.open_held_preexisting_authority(
        native_session_set=session_set,
        run_spec=run_spec,
        authority_name=name,
        expected_raw_sha256=descriptor["raw_sha256"],
    )


def _produce_role(
    session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    role: str,
) -> tuple[
    native_client.HeldRoleProduction,
    native_client.HeldRegisteredCas,
    dict[str, Any],
]:
    production = native_client.produce_role_artifact(
        session_set=session_set,
        run_spec=run_spec,
        role=role,
    )
    evidence = native_client.postverify_role_production(
        session_set=session_set,
        run_spec=run_spec,
        production=production,
        expected_role=role,
    )
    held_cas = contract.open_held_compound_cas(
        native_session_set=session_set,
        run_spec=run_spec,
        namespace_name=role,
        expected_raw_sha256=evidence["raw_sha256"],
    )
    return production, held_cas, evidence


def _prepare_full_closure(
    tmp_path: Path,
    executable: Path,
) -> _PreparedClosure:
    fixture = _build_physical_fixture(tmp_path, executable)
    session_set, run_spec, run_spec_evidence = _open_run_spec(fixture)
    root_lease = native_client.acquire_compound_root_lease(
        session_set=session_set,
        run_spec=run_spec,
    )
    decision = _hold_preexisting(
        fixture,
        session_set,
        run_spec,
        "factor_v2_terminal_decision_authority",
    )
    low_rvol = _hold_preexisting(
        fixture,
        session_set,
        run_spec,
        "factor_v2_low_rvol_branch_authority",
    )
    started = contract.start_compound_run_with_low_rvol_gate(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        factor_v2_terminal_decision_authority_cas=decision,
        low_rvol_branch_authority_cas=low_rvol,
    )
    assert started["start_run_performed"] is True
    role_productions: dict[str, native_client.HeldRoleProduction] = {}
    role_artifacts: dict[str, native_client.HeldRegisteredCas] = {}
    role_evidence: dict[str, dict[str, Any]] = {}
    for role in contract.PRODUCER_ROLE_NAMES:
        production, held_cas, evidence = _produce_role(session_set, run_spec, role)
        role_productions[role] = production
        role_artifacts[role] = held_cas
        role_evidence[role] = evidence
    native_run = native_client.acquire_native_run_completion(
        session_set=session_set,
        root_lease=root_lease,
        run_spec=run_spec,
        parent_producer=role_productions["parent_producer"],
        evaluator_producer=role_productions["evaluator_producer"],
    )
    native_run_evidence = native_client.postverify_native_completion(
        session_set=session_set,
        completion=native_run,
        expected_phase="run",
        run_spec=run_spec,
        root_lease=root_lease,
    )
    assert tuple(native_run_evidence["raw_binding_names"]) == (
        contract.NATIVE_RUN_RAW_BINDINGS
    )
    contract.validate_native_run_exact_closure(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        parent_producer_cas=role_artifacts["parent_producer"],
        evaluator_producer_cas=role_artifacts["evaluator_producer"],
        parent_producer_production=role_productions["parent_producer"],
        evaluator_producer_production=role_productions["evaluator_producer"],
        native_run_completion=native_run,
    )
    compound_run = contract.publish_compound_run_receipt(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        parent_producer_cas=role_artifacts["parent_producer"],
        evaluator_producer_cas=role_artifacts["evaluator_producer"],
        parent_producer_production=role_productions["parent_producer"],
        evaluator_producer_production=role_productions["evaluator_producer"],
        native_run_completion=native_run,
    )
    compound_run_evidence = contract.postverify_held_compound_cas(
        native_session_set=session_set,
        run_spec=run_spec,
        held_cas=compound_run,
    )
    assert tuple(compound_run_evidence["raw_binding_names"]) == (
        contract.RUN_RECEIPT_RAW_BINDINGS
    )
    native_client.transition_compound_root_epoch(
        session_set=session_set,
        root_lease=root_lease,
        run_spec=run_spec,
        transition="START_VERIFY",
    )
    for role in contract.VERIFIER_ROLE_NAMES:
        production, held_cas, evidence = _produce_role(session_set, run_spec, role)
        role_productions[role] = production
        role_artifacts[role] = held_cas
        role_evidence[role] = evidence
    contract.validate_shared_compound_attempt(
        native_session_set=session_set,
        run_spec=run_spec,
        role_artifacts=role_artifacts,
        role_productions=role_productions,
    )
    native_verify = native_client.acquire_native_verify_completion(
        session_set=session_set,
        root_lease=root_lease,
        run_spec=run_spec,
        compound_run_receipt=compound_run,
        parent_verifier=role_productions["parent_verifier"],
        evaluator_verifier=role_productions["evaluator_verifier"],
    )
    native_verify_evidence = native_client.postverify_native_completion(
        session_set=session_set,
        completion=native_verify,
        expected_phase="verify",
        run_spec=run_spec,
        root_lease=root_lease,
    )
    assert tuple(native_verify_evidence["raw_binding_names"]) == (
        contract.NATIVE_VERIFY_RAW_BINDINGS
    )
    contract.validate_native_verify_exact_closure(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        compound_run_receipt_cas=compound_run,
        parent_verifier_cas=role_artifacts["parent_verifier"],
        evaluator_verifier_cas=role_artifacts["evaluator_verifier"],
        parent_verifier_production=role_productions["parent_verifier"],
        evaluator_verifier_production=role_productions["evaluator_verifier"],
        native_verify_completion=native_verify,
    )
    compound_terminal = contract.publish_compound_terminal_receipt(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        compound_run_receipt_cas=compound_run,
        parent_verifier_cas=role_artifacts["parent_verifier"],
        evaluator_verifier_cas=role_artifacts["evaluator_verifier"],
        parent_verifier_production=role_productions["parent_verifier"],
        evaluator_verifier_production=role_productions["evaluator_verifier"],
        native_verify_completion=native_verify,
    )
    compound_terminal_evidence = contract.postverify_held_compound_cas(
        native_session_set=session_set,
        run_spec=run_spec,
        held_cas=compound_terminal,
    )
    assert tuple(compound_terminal_evidence["raw_binding_names"]) == (
        contract.TERMINAL_RECEIPT_RAW_BINDINGS
    )
    native_terminal = native_client.acquire_native_terminal_authority(
        session_set=session_set,
        root_lease=root_lease,
        run_spec=run_spec,
        parent_producer=role_productions["parent_producer"],
        parent_verifier=role_productions["parent_verifier"],
        evaluator_producer=role_productions["evaluator_producer"],
        evaluator_verifier=role_productions["evaluator_verifier"],
        compound_run_receipt=compound_run,
        compound_terminal_receipt=compound_terminal,
        native_run_completion=native_run,
        native_verify_completion=native_verify,
    )
    native_terminal_evidence = native_client.postverify_native_completion(
        session_set=session_set,
        completion=native_terminal,
        expected_phase="terminal",
        run_spec=run_spec,
        root_lease=root_lease,
    )
    assert tuple(native_terminal_evidence["raw_binding_names"]) == (
        contract.NATIVE_TERMINAL_RAW_BINDINGS
    )
    contract.validate_native_terminal_exact_closure(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        role_artifacts=role_artifacts,
        role_productions=role_productions,
        compound_run_receipt_cas=compound_run,
        compound_terminal_receipt_cas=compound_terminal,
        native_run_completion=native_run,
        native_verify_completion=native_verify,
        native_terminal_authority=native_terminal,
    )
    contract.authorize_compound_authority(
        native_session_set=session_set,
        root_lease_capability=root_lease,
        run_spec=run_spec,
        role_artifacts=role_artifacts,
        role_productions=role_productions,
        compound_run_receipt_cas=compound_run,
        compound_terminal_receipt_cas=compound_terminal,
        native_run_completion=native_run,
        native_verify_completion=native_verify,
        native_terminal_authority=native_terminal,
    )
    return _PreparedClosure(
        fixture=fixture,
        session_set=session_set,
        run_spec=run_spec,
        run_spec_evidence=run_spec_evidence,
        root_lease=root_lease,
        role_productions=role_productions,
        role_artifacts=role_artifacts,
        role_evidence=role_evidence,
        compound_run_receipt=compound_run,
        compound_run_evidence=compound_run_evidence,
        compound_terminal_receipt=compound_terminal,
        compound_terminal_evidence=compound_terminal_evidence,
        native_run=native_run,
        native_verify=native_verify,
        native_terminal=native_terminal,
    )


@pytest.mark.parametrize(
    "violation",
    [
        "overlap",
        "nested",
        "input-overlap",
        "wrong-category",
        "unknown-field",
        "noncanonical",
        "nonempty",
        "staging",
        "reparse",
        "dacl",
        "source-none",
        "signature",
    ],
)
def test_physical_manifest_rejects_policy_namespace_and_registration_drift(
    violation: str,
    tmp_path: Path,
    compiled_broker: Path,
) -> None:
    fixture = _build_physical_fixture(
        tmp_path,
        compiled_broker,
        policy_violation=violation,
    )
    before = {
        path: tuple(path.rglob("*"))
        for path in (
            Path(value["canonical_root"])
            for value in fixture.policy["namespaces"].values()
            if Path(value["canonical_root"]).exists()
        )
    }
    session_set = _open_session_set(fixture)
    try:
        with pytest.raises(
            (
                native_client.FactorAuthorityCompoundNativeClientError,
                contract.FactorAuthorityCompoundContractV2Error,
            ),
            match="policy|namespace|registration|signature|DACL|reparse|empty",
        ):
            contract.open_registered_compound_deployment_policy_authority(
                native_session_set=session_set
            )
    finally:
        native_client.close_compound_native_session_set(session_set)
    after = {path: tuple(path.rglob("*")) for path in before}
    assert after == before


@pytest.mark.parametrize(
    "violation",
    [
        "valid-held",
        "plain-mapping",
        "python-forgery",
        "hardlink",
        "replacement",
        "raw",
        "self",
        "payload-root",
        "category",
        "role",
        "attempt",
        "global",
        "run-spec",
        "size",
        "file-id",
        "unknown-field",
        "extra-staging",
        "mid-read",
        "aba",
    ],
)
def test_native_held_cas_detects_handle_namespace_and_content_tamper(
    violation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    compiled_broker: Path,
) -> None:
    prepared = _prepare_full_closure(tmp_path, compiled_broker)
    held = prepared.role_artifacts["parent_producer"]
    evidence = prepared.role_evidence["parent_producer"]
    path = Path(evidence["path"])
    try:
        tamper_committed = False
        writer_blocked = False
        probe_state = {"attempted": False, "write_succeeded": False, "write_blocked": False}
        if violation == "plain-mapping":
            candidate: object = evidence
        elif violation == "python-forgery":
            candidate = object.__new__(native_client.HeldRegisteredCas)
        else:
            candidate = held
        if violation == "hardlink":
            try:
                os.link(path, tmp_path / "second-link.json")
            except OSError:
                writer_blocked = True
            else:
                tamper_committed = True
        elif violation == "replacement":
            replacement = tmp_path / "replacement.json"
            replacement.write_bytes(path.read_bytes())
            try:
                replacement.replace(path)
            except OSError:
                writer_blocked = True
            else:
                tamper_committed = True
        elif violation in {
            "raw",
            "self",
            "payload-root",
            "category",
            "role",
            "attempt",
            "global",
            "run-spec",
            "size",
            "unknown-field",
        }:
            original = path.read_bytes()
            document = json.loads(original)
            field = {
                "self": "artifact_sha256",
                "payload-root": "payload_root_sha256",
                "category": "category",
                "role": "role",
                "attempt": "attempt_key_sha256",
                "global": "global_attempt_identity_sha256",
                "run-spec": "run_spec_raw_sha256",
                "unknown-field": "unknown",
            }.get(violation)
            if violation == "raw":
                replacement_raw = b"raw-drift"
            elif violation == "size":
                replacement_raw = original + b" "
            else:
                assert field is not None
                document[field] = True if field == "unknown" else _sha(violation)
                replacement_raw = _canonical_bytes(document)
            try:
                path.write_bytes(replacement_raw)
            except OSError:
                writer_blocked = True
                assert path.read_bytes() == original
            else:
                tamper_committed = True
        elif violation == "file-id":
            replacement = tmp_path / "different-file-id.json"
            replacement.write_bytes(path.read_bytes())
            try:
                replacement.replace(path)
            except OSError:
                writer_blocked = True
            else:
                tamper_committed = True
        elif violation == "extra-staging":
            root = Path(
                prepared.fixture.policy["namespaces"]["parent_producer"][
                    "canonical_root"
                ]
            )
            try:
                (root / ".extra.partial").write_bytes(b"partial")
            except OSError:
                writer_blocked = True
            else:
                tamper_committed = True
        elif violation in {"mid-read", "aba"}:
            original = path.read_bytes()

            def mutate(stage: str, observed_path: str) -> None:
                if (
                    stage == "after-first-read"
                    and Path(observed_path) == path
                    and not probe_state["attempted"]
                ):
                    probe_state["attempted"] = True
                    try:
                        path.write_bytes(b"mid-read-replacement")
                    except OSError:
                        probe_state["write_blocked"] = True
                        return
                    probe_state["write_succeeded"] = True
                    if violation == "aba":
                        try:
                            path.write_bytes(original)
                        except OSError:
                            pass

            monkeypatch.setattr(contract, "_held_cas_read_probe", mutate)

        def postverify() -> dict[str, Any]:
            return contract.postverify_held_compound_cas(
                native_session_set=prepared.session_set,
                run_spec=prepared.run_spec,
                held_cas=candidate,  # type: ignore[arg-type]
            )

        def assert_valid(verified: dict[str, Any]) -> None:
            assert verified["same_handle_postverified"] is True
            assert verified["ancestor_handles_held"] is True
            assert verified["owner_dacl_measured"] is True
            assert verified["nlink"] == 1
            assert verified["raw_sha256"] == evidence["raw_sha256"]

        domain_errors = (
            native_client.FactorAuthorityCompoundNativeClientError,
            contract.FactorAuthorityCompoundContractV2Error,
        )
        if violation in {"plain-mapping", "python-forgery"} or tamper_committed:
            with pytest.raises(
                domain_errors,
                match="held|opaque|tamper|drift|namespace|payload|DACL",
            ):
                postverify()
        elif violation in {"mid-read", "aba"}:
            try:
                verified = postverify()
            except domain_errors:
                assert probe_state["write_succeeded"] is True
            else:
                assert probe_state["write_blocked"] is True
                assert_valid(verified)
        else:
            assert violation == "valid-held" or writer_blocked is True
            verified = contract.postverify_held_compound_cas(
                native_session_set=prepared.session_set,
                run_spec=prepared.run_spec,
                held_cas=held,
            )
            assert_valid(verified)
    finally:
        native_client.close_compound_native_session_set(prepared.session_set)


@pytest.mark.parametrize(
    "variant",
    ["valid", "decision-drift", "branch-drift", "absent", "relation-mismatch"],
)
def test_low_rvol_gate_uses_preexisting_related_authorities_before_start_run(
    variant: str,
    tmp_path: Path,
    compiled_broker: Path,
) -> None:
    fixture = _build_physical_fixture(
        tmp_path,
        compiled_broker,
        relation_mismatch=variant == "relation-mismatch",
    )
    session_set, run_spec, _run_spec_evidence = _open_run_spec(fixture)
    root_lease = native_client.acquire_compound_root_lease(
        session_set=session_set,
        run_spec=run_spec,
    )
    for role in ("evaluator_producer", "evaluator_verifier"):
        namespace = fixture.policy["namespaces"][role]
        assert not any(Path(namespace["canonical_root"]).rglob("*"))
    try:
        if variant == "decision-drift":
            Path(
                fixture.preexisting["factor_v2_terminal_decision_authority"]["path"]
            ).write_bytes(b"decision-drift")
        elif variant == "branch-drift":
            Path(
                fixture.preexisting["factor_v2_low_rvol_branch_authority"]["path"]
            ).write_bytes(b"branch-drift")

        def start() -> dict[str, Any]:
            decision = _hold_preexisting(
                fixture,
                session_set,
                run_spec,
                "factor_v2_terminal_decision_authority",
            )
            branch = None
            if variant != "absent":
                branch = _hold_preexisting(
                    fixture,
                    session_set,
                    run_spec,
                    "factor_v2_low_rvol_branch_authority",
                )
            return contract.start_compound_run_with_low_rvol_gate(
                native_session_set=session_set,
                root_lease_capability=root_lease,
                run_spec=run_spec,
                factor_v2_terminal_decision_authority_cas=decision,
                low_rvol_branch_authority_cas=branch,
            )

        if variant == "valid":
            result = start()
            assert result["selected_branch"] == branch_selector.LOW_RVOL_BRANCH
            assert result["start_run_performed"] is True
        else:
            with pytest.raises(
                (
                    native_client.FactorAuthorityCompoundNativeClientError,
                    contract.FactorAuthorityCompoundContractV2Error,
                ),
                match="low-rvol|decision|branch|relation|held|raw",
            ):
                start()
            root_evidence = native_client.postverify_compound_root_lease(
                session_set=session_set,
                root_lease=root_lease,
                run_spec=run_spec,
            )
            assert root_evidence["epoch_raw_sha256"] == {}
            ledger_root = Path(
                fixture.policy["namespaces"]["global_attempt_ledger"][
                    "canonical_root"
                ]
            )
            assert not any(ledger_root.rglob("*.json"))
    finally:
        native_client.close_compound_native_session_set(session_set)


@pytest.mark.parametrize(
    "variant",
    [
        "empty",
        "missing",
        "replacement",
        "role-swap",
        "epoch-swap",
        "run-receipt-swap",
        "terminal-receipt-swap",
        "v1-terminal",
        "extra-success",
        "full-injected",
    ],
)
def test_publication_is_last_and_only_full_exact_closure_reaches_hook(
    variant: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    compiled_broker: Path,
) -> None:
    prepared = _prepare_full_closure(tmp_path / "primary", compiled_broker)
    alternate: _PreparedClosure | None = None
    roles = dict(prepared.role_artifacts)
    productions = dict(prepared.role_productions)
    root_lease = prepared.root_lease
    run_receipt: object = prepared.compound_run_receipt
    terminal_receipt: object = prepared.compound_terminal_receipt
    native_terminal: object = prepared.native_terminal
    if variant in {
        "replacement",
        "epoch-swap",
        "run-receipt-swap",
        "terminal-receipt-swap",
    }:
        alternate = _prepare_full_closure(tmp_path / "alternate", compiled_broker)
    if variant == "empty":
        roles = {}
        productions = {}
    elif variant == "missing":
        roles.pop("parent_verifier")
        productions.pop("parent_verifier")
    elif variant == "replacement":
        assert alternate is not None
        roles["parent_producer"] = alternate.role_artifacts["parent_producer"]
        productions["parent_producer"] = alternate.role_productions[
            "parent_producer"
        ]
    elif variant == "role-swap":
        roles["parent_producer"], roles["evaluator_producer"] = (
            roles["evaluator_producer"],
            roles["parent_producer"],
        )
        productions["parent_producer"], productions["evaluator_producer"] = (
            productions["evaluator_producer"],
            productions["parent_producer"],
        )
    elif variant == "epoch-swap":
        assert alternate is not None
        root_lease = alternate.root_lease
    elif variant == "run-receipt-swap":
        assert alternate is not None
        run_receipt = alternate.compound_run_receipt
    elif variant == "terminal-receipt-swap":
        assert alternate is not None
        terminal_receipt = alternate.compound_terminal_receipt
    elif variant == "v1-terminal":
        native_terminal = {
            "schema": "factor-authority-compound-native-terminal/v1",
            "verified": True,
        }
    elif variant == "extra-success":
        success = prepared.fixture.policy["namespaces"]["success"]
        (Path(success["canonical_root"]) / ".unexpected").write_bytes(b"x")
    verification_calls: list[str] = []
    publication_calls: list[str] = []
    monkeypatch.setattr(
        contract,
        "_publication_probe",
        lambda stage, held: verification_calls.append(stage),
    )

    def injected_success(*args: object, **kwargs: object) -> None:
        del args, kwargs
        publication_calls.append("success-hook")
        raise contract.FactorAuthorityCompoundPublicationFailure(
            "injected before success CAS"
        )

    monkeypatch.setattr(contract, "_publish_success_cas", injected_success)
    success = prepared.fixture.policy["namespaces"]["success"]
    success_category = (
        Path(success["canonical_root"]) / success["expected_category"]
    )
    try:
        if variant == "full-injected":
            with pytest.raises(
                contract.FactorAuthorityCompoundPublicationFailure,
                match="injected before success CAS",
            ):
                contract.publish_compound_authority(
                    native_session_set=prepared.session_set,
                    root_lease_capability=root_lease,
                    run_spec=prepared.run_spec,
                    role_artifacts=roles,
                    role_productions=productions,
                    compound_run_receipt_cas=run_receipt,  # type: ignore[arg-type]
                    compound_terminal_receipt_cas=terminal_receipt,  # type: ignore[arg-type]
                    native_run_completion=prepared.native_run,
                    native_verify_completion=prepared.native_verify,
                    native_terminal_authority=native_terminal,  # type: ignore[arg-type]
                )
            assert len(verification_calls) == 1
            assert len(publication_calls) == 1
        else:
            with pytest.raises(
                (
                    native_client.FactorAuthorityCompoundNativeClientError,
                    contract.FactorAuthorityCompoundContractV2Error,
                ),
                match="closure|held|binding|role|epoch|success|opaque|v2",
            ):
                contract.publish_compound_authority(
                    native_session_set=prepared.session_set,
                    root_lease_capability=root_lease,
                    run_spec=prepared.run_spec,
                    role_artifacts=roles,
                    role_productions=productions,
                    compound_run_receipt_cas=run_receipt,  # type: ignore[arg-type]
                    compound_terminal_receipt_cas=terminal_receipt,  # type: ignore[arg-type]
                    native_run_completion=prepared.native_run,
                    native_verify_completion=prepared.native_verify,
                    native_terminal_authority=native_terminal,  # type: ignore[arg-type]
                )
            assert verification_calls == []
            assert publication_calls == []
        assert not success_category.exists() or not any(success_category.rglob("*"))
    finally:
        if alternate is not None:
            native_client.close_compound_native_session_set(alternate.session_set)
        native_client.close_compound_native_session_set(prepared.session_set)
