from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app import factor_authority_compound_contract_v2 as contract
from app import factor_v2_decision_branch_selector as branch_selector
from app import factor_v3_formal_trusted_supervisor as root_lease


PARENT_SEMANTIC_INPUT_ROOT_SHA256 = hashlib.sha256(
    b"pre-existing-parent-semantic-input"
).hexdigest()
EVALUATOR_SEMANTIC_INPUT_ROOT_SHA256 = hashlib.sha256(
    b"pre-existing-evaluator-semantic-input"
).hexdigest()
REGISTERED_SHA_BY_NAME = {
    "native_tcb": hashlib.sha256(b"registered-native-tcb").hexdigest(),
    "parent_producer": hashlib.sha256(
        b"registered-parent-producer-source-authority"
    ).hexdigest(),
    "parent_verifier": hashlib.sha256(
        b"registered-parent-verifier-source-authority"
    ).hexdigest(),
    "evaluator_producer": hashlib.sha256(
        b"registered-evaluator-producer-source-authority"
    ).hexdigest(),
    "evaluator_verifier": hashlib.sha256(
        b"registered-evaluator-verifier-source-authority"
    ).hexdigest(),
}


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


def _identity_fixture() -> dict[str, Any]:
    semantic_identity = {
        "schema": contract.COMPOUND_SEMANTIC_IDENTITY_SCHEMA,
        "parent_semantic_input_root_sha256": PARENT_SEMANTIC_INPUT_ROOT_SHA256,
        "evaluator_semantic_input_root_sha256": (
            EVALUATOR_SEMANTIC_INPUT_ROOT_SHA256
        ),
    }
    semantic_root = hashlib.sha256(_canonical_bytes(semantic_identity)).hexdigest()
    attempt = root_lease.factor_v3_parent_source_derive_attempt_key_sha256(
        semantic_root
    )
    global_identity = (
        root_lease.factor_v3_parent_source_derive_global_attempt_identity_sha256(
            attempt
        )
    )
    return {
        "semantic_identity": semantic_identity,
        "semantic_input_root_sha256": semantic_root,
        "attempt_key_sha256": attempt,
        "global_attempt_identity_sha256": global_identity,
    }


def _four_namespaces(parent: Path) -> dict[str, dict[str, str]]:
    """Return namespace declarations without creating directories or outputs."""

    return {
        role: {
            "role": role,
            "canonical_root": str((parent / role.replace("_", "-")).resolve()),
            "expected_category": contract.EXPECTED_CATEGORY_BY_ROLE[role],
        }
        for role in contract.AUTHORITY_CAS_ROLES
    }


def _set_test_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contract,
        "REGISTERED_COMPOUND_NATIVE_TCB_SHA256",
        REGISTERED_SHA_BY_NAME["native_tcb"],
    )
    for role in contract.AUTHORITY_CAS_ROLES:
        monkeypatch.setattr(
            contract,
            f"REGISTERED_{role.upper()}_SOURCE_AUTHORITY_SHA256",
            REGISTERED_SHA_BY_NAME[role],
        )


def _registered_binding() -> dict[str, str]:
    return dict(REGISTERED_SHA_BY_NAME)


def _run_spec_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _set_test_registries(monkeypatch)
    namespace_parent = tmp_path / "role-cas"
    namespace_parent.mkdir()
    namespaces = _four_namespaces(namespace_parent)
    for namespace in namespaces.values():
        Path(namespace["canonical_root"]).mkdir()
    payload = {
        "schema": contract.COMPOUND_RUN_SPEC_SCHEMA,
        "identity_binding": _identity_fixture(),
        "cas_namespaces": namespaces,
        "registered_authority_sha256": _registered_binding(),
        "test_fixture_only": True,
    }
    return {
        **payload,
        "run_spec_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    }


def _write_cas_payload(
    *,
    root: Path,
    category: str,
    payload: Mapping[str, Any],
    descriptor_fields: Mapping[str, Any],
) -> dict[str, Any]:
    self_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    artifact = {**payload, "artifact_sha256": self_sha256}
    raw = _canonical_bytes(artifact)
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    path = root / category / "sha256" / raw_sha256[:2] / f"{raw_sha256}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "schema": contract.COMPOUND_CAS_DESCRIPTOR_SCHEMA,
        **descriptor_fields,
        "category": category,
        "path": str(path.resolve()),
        "raw_sha256": raw_sha256,
        "self_sha256": self_sha256,
    }


def _write_role_cas(
    run_spec: Mapping[str, Any],
    role: str,
    *,
    payload_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = run_spec["identity_binding"]
    namespace = run_spec["cas_namespaces"][role]
    payload = {
        "schema": contract.COMPOUND_ROLE_OUTPUT_SCHEMA,
        "role": role,
        "category": namespace["expected_category"],
        "attempt_key_sha256": identity["attempt_key_sha256"],
        "global_attempt_identity_sha256": identity[
            "global_attempt_identity_sha256"
        ],
        "run_spec_sha256": run_spec["run_spec_sha256"],
        "source_authority_sha256": run_spec["registered_authority_sha256"][role],
        "payload_sha256": _sha(f"payload:{role}"),
        **dict(payload_extra or {}),
    }
    return _write_cas_payload(
        root=Path(namespace["canonical_root"]),
        category=namespace["expected_category"],
        payload=payload,
        descriptor_fields={
            "role": role,
            "attempt_key_sha256": identity["attempt_key_sha256"],
            "global_attempt_identity_sha256": identity[
                "global_attempt_identity_sha256"
            ],
            "run_spec_sha256": run_spec["run_spec_sha256"],
        },
    )


def _write_auxiliary_cas(
    root: Path,
    run_spec: Mapping[str, Any],
    *,
    category: str,
    bindings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = run_spec["identity_binding"]
    payload = {
        "schema": f"{category}/v2",
        "category": category,
        "attempt_key_sha256": identity["attempt_key_sha256"],
        "global_attempt_identity_sha256": identity[
            "global_attempt_identity_sha256"
        ],
        "run_spec_sha256": run_spec["run_spec_sha256"],
        "native_tcb_sha256": run_spec["registered_authority_sha256"]["native_tcb"],
        "bindings": dict(bindings or {}),
    }
    return _write_cas_payload(
        root=root,
        category=category,
        payload=payload,
        descriptor_fields={
            "attempt_key_sha256": identity["attempt_key_sha256"],
            "global_attempt_identity_sha256": identity[
                "global_attempt_identity_sha256"
            ],
            "run_spec_sha256": run_spec["run_spec_sha256"],
        },
    )


def _receipt_namespace(root: Path, category: str) -> dict[str, str]:
    root.mkdir()
    return {
        "canonical_root": str(root.resolve()),
        "expected_category": category,
    }


@contextmanager
def _real_root_lease_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_spec: Mapping[str, Any],
    *,
    state: str,
) -> Iterator[tuple[object, dict[str, Path]]]:
    identity = run_spec["identity_binding"]
    run_spec_sha256 = run_spec["run_spec_sha256"]
    attempt = identity["attempt_key_sha256"]
    global_identity = identity["global_attempt_identity_sha256"]
    ledger_root = tmp_path / "root-lease"
    ledger_root.mkdir(parents=True)
    run_claim, verify_claim = root_lease.factor_v3_parent_source_global_claim_paths(
        ledger_root,
        attempt,
    )
    run_claim.parent.mkdir(parents=True)
    monkeypatch.setattr(
        root_lease,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        ledger_root,
    )
    monkeypatch.setattr(
        root_lease,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        root_lease.factor_v3_parent_source_root_policy_sha256(ledger_root),
    )
    monkeypatch.setattr(
        root_lease,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        root_lease._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    with root_lease._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
        root_lease._PARENT_SOURCE_ROOT_BINDINGS.pop(global_identity, None)
        root_lease._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(global_identity)
    status, capability = root_lease._ParentSourceRootLease.acquire(
        global_attempt_ledger_root=ledger_root,
        global_run_claim_path=run_claim,
        global_verify_claim_path=verify_claim,
        run_spec_sha256=run_spec_sha256,
        attempt_key_sha256=attempt,
        global_attempt_identity_sha256=global_identity,
        timeout_milliseconds=2_000,
    )
    assert status == root_lease.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert capability is not None
    try:
        assert root_lease.factor_v3_parent_source_lookup_root_epoch_transition(
            capability,
            run_spec_sha256=run_spec_sha256,
            attempt_key_sha256=attempt,
            global_attempt_identity_sha256=global_identity,
            requested_action=root_lease.PARENT_SOURCE_ROOT_ACTION_RUN,
        ) == (
            root_lease.PARENT_SOURCE_ROOT_STATE_EMPTY,
            root_lease.PARENT_SOURCE_ROOT_TRANSITION_START_RUN,
        )
        if state in {"verify_claimed", "terminal"}:
            capability.publish_run_receipt(
                run_spec_sha256=run_spec_sha256,
                attempt_key_sha256=attempt,
                global_attempt_identity_sha256=global_identity,
            )
            assert root_lease.factor_v3_parent_source_lookup_root_epoch_transition(
                capability,
                run_spec_sha256=run_spec_sha256,
                attempt_key_sha256=attempt,
                global_attempt_identity_sha256=global_identity,
                requested_action=root_lease.PARENT_SOURCE_ROOT_ACTION_VERIFY,
            ) == (
                root_lease.PARENT_SOURCE_ROOT_STATE_COMPLETED,
                root_lease.PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY,
            )
        if state == "terminal":
            capability.publish_terminal_receipt(
                run_spec_sha256=run_spec_sha256,
                attempt_key_sha256=attempt,
                global_attempt_identity_sha256=global_identity,
            )
        files = {
            "run.claim.json": run_claim,
            "run.receipt.json": run_claim.parent / "run.receipt.json",
            "verify.claim.json": verify_claim,
            "terminal.receipt.json": run_claim.parent / "terminal.receipt.json",
        }
        yield capability, files
    finally:
        capability.close()
        with root_lease._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            root_lease._PARENT_SOURCE_ROOT_BINDINGS.pop(global_identity, None)
            root_lease._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(global_identity)


def _contains_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


def test_registry_none_rejects_before_any_namespace_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_names = (
        "REGISTERED_COMPOUND_NATIVE_TCB_SHA256",
        "REGISTERED_PARENT_PRODUCER_SOURCE_AUTHORITY_SHA256",
        "REGISTERED_PARENT_VERIFIER_SOURCE_AUTHORITY_SHA256",
        "REGISTERED_EVALUATOR_PRODUCER_SOURCE_AUTHORITY_SHA256",
        "REGISTERED_EVALUATOR_VERIFIER_SOURCE_AUTHORITY_SHA256",
    )
    for name in registry_names:
        monkeypatch.setattr(contract, name, None)
    parent = tmp_path / "must-remain-absent"
    namespaces = _four_namespaces(parent)
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="registered.*authority",
    ):
        contract.build_compound_run_spec(
            identity_binding=_identity_fixture(),
            cas_namespace_parent=parent,
            cas_namespaces=namespaces,
            test_fixture_only=False,
        )
    assert not parent.exists()


def test_identity_binds_only_preexisting_semantic_roots_and_is_path_independent(
    tmp_path: Path,
) -> None:
    expected = _identity_fixture()
    left = contract.build_compound_identity_binding(
        parent_semantic_input_root_sha256=PARENT_SEMANTIC_INPUT_ROOT_SHA256,
        evaluator_semantic_input_root_sha256=EVALUATOR_SEMANTIC_INPUT_ROOT_SHA256,
    )
    (tmp_path / "unrelated-location-a").mkdir()
    right = contract.build_compound_identity_binding(
        parent_semantic_input_root_sha256=PARENT_SEMANTIC_INPUT_ROOT_SHA256,
        evaluator_semantic_input_root_sha256=EVALUATOR_SEMANTIC_INPUT_ROOT_SHA256,
    )
    assert left == right == expected
    serialized = json.dumps(left, sort_keys=True)
    assert not any(
        forbidden in serialized
        for forbidden in ("path", "artifact", "receipt", "output", "namespace")
    )


def test_run_spec_binds_only_four_empty_namespaces_and_five_registrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_test_registries(monkeypatch)
    parent = tmp_path / "role-cas"
    parent.mkdir()
    namespaces = _four_namespaces(parent)
    for namespace in namespaces.values():
        Path(namespace["canonical_root"]).mkdir()
    result = contract.build_compound_run_spec(
        identity_binding=_identity_fixture(),
        cas_namespace_parent=parent,
        cas_namespaces=namespaces,
        test_fixture_only=True,
    )
    assert set(result) == {
        "schema",
        "identity_binding",
        "cas_namespaces",
        "registered_authority_sha256",
        "test_fixture_only",
        "run_spec_sha256",
    }
    assert result["cas_namespaces"] == namespaces
    assert result["registered_authority_sha256"] == _registered_binding()
    assert all(not any(Path(item["canonical_root"]).iterdir()) for item in namespaces.values())


@pytest.mark.parametrize("violation", ["overlap", "nested", "escape"])
def test_run_spec_rejects_overlapping_nested_or_escaped_namespaces(
    violation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_test_registries(monkeypatch)
    parent = tmp_path / "role-cas"
    parent.mkdir()
    namespaces = _four_namespaces(parent)
    if violation == "overlap":
        namespaces["evaluator_producer"]["canonical_root"] = namespaces[
            "parent_producer"
        ]["canonical_root"]
    elif violation == "nested":
        namespaces["evaluator_producer"]["canonical_root"] = str(
            (Path(namespaces["parent_producer"]["canonical_root"]) / "nested").resolve()
        )
    else:
        namespaces["evaluator_producer"]["canonical_root"] = str(
            (tmp_path / "escaped-role-cas").resolve()
        )
    for namespace in namespaces.values():
        Path(namespace["canonical_root"]).mkdir(parents=True, exist_ok=True)
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="namespace.*overlap|namespace.*escape|empty namespace",
    ):
        contract.build_compound_run_spec(
            identity_binding=_identity_fixture(),
            cas_namespace_parent=parent,
            cas_namespaces=namespaces,
            test_fixture_only=True,
        )


@pytest.mark.parametrize(
    "violation",
    ["path", "raw", "self", "category", "role", "attempt", "global", "run_spec"],
)
def test_role_cas_rejects_physical_or_compound_identity_drift(
    violation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    artifact = _write_role_cas(spec, "parent_producer")
    drifted = deepcopy(artifact)
    if violation == "path":
        escaped = tmp_path / "escaped.json"
        escaped.write_bytes(Path(artifact["path"]).read_bytes())
        drifted["path"] = str(escaped.resolve())
    elif violation in {"raw", "self"}:
        drifted[f"{violation}_sha256"] = _sha(f"drift:{violation}")
    elif violation in {"attempt", "global"}:
        field = {
            "attempt": "attempt_key_sha256",
            "global": "global_attempt_identity_sha256",
        }[violation]
        drifted[field] = _sha(f"drift:{violation}")
    elif violation == "run_spec":
        drifted["run_spec_sha256"] = _sha("drift:run-spec")
    else:
        drifted[violation] = f"wrong-{violation}"
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="physical.*CAS|compound.*identity",
    ):
        contract.validate_compound_role_cas(
            run_spec=spec,
            role="parent_producer",
            artifact=drifted,
        )


def test_parent_and_evaluator_attempt_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    artifacts = {
        role: _write_role_cas(spec, role) for role in contract.AUTHORITY_CAS_ROLES
    }
    artifacts["evaluator_verifier"]["attempt_key_sha256"] = _sha(
        "different-compound-attempt"
    )
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="shared.*attempt",
    ):
        contract.validate_shared_compound_attempt(
            run_spec=spec,
            artifacts_by_role=artifacts,
        )


def test_run_receipt_binds_two_producers_and_native_completion_via_real_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    parent = _write_role_cas(spec, "parent_producer")
    evaluator = _write_role_cas(spec, "evaluator_producer")
    native = _write_auxiliary_cas(
        tmp_path / "native-run",
        spec,
        category="factor-authority-native-run-completion",
    )
    namespace = _receipt_namespace(
        tmp_path / "compound-run-receipt",
        "factor-authority-compound-run-receipt",
    )
    latest_predecessor_ctime_ns = max(
        Path(item["path"]).stat().st_ctime_ns for item in (parent, evaluator, native)
    )
    with _real_root_lease_capability(
        monkeypatch,
        tmp_path / "lease",
        spec,
        state="claimed",
    ) as (capability, epoch_files):
        result = contract.publish_compound_run_receipt(
            receipt_namespace=namespace,
            root_lease_capability=capability,
            run_spec=spec,
            parent_producer_cas=parent,
            evaluator_producer_cas=evaluator,
            native_run_completion_cas=native,
        )
        payload = json.loads(Path(result["path"]).read_bytes())
        assert payload["bindings"] == {
            "parent_producer_raw_sha256": parent["raw_sha256"],
            "evaluator_producer_raw_sha256": evaluator["raw_sha256"],
            "native_run_completion_raw_sha256": native["raw_sha256"],
        }
        assert epoch_files["run.receipt.json"].is_file()
        assert Path(result["path"]).stat().st_ctime_ns >= latest_predecessor_ctime_ns
        assert (
            epoch_files["run.receipt.json"].stat().st_ctime_ns
            >= latest_predecessor_ctime_ns
        )
        assert not _contains_key(payload, "commit_order")


def test_terminal_receipt_binds_run_verifiers_native_and_ids_via_real_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    run_receipt = _write_auxiliary_cas(
        tmp_path / "existing-run-receipt",
        spec,
        category="factor-authority-compound-run-receipt",
    )
    parent = _write_role_cas(spec, "parent_verifier")
    evaluator = _write_role_cas(spec, "evaluator_verifier")
    native = _write_auxiliary_cas(
        tmp_path / "native-verify",
        spec,
        category="factor-authority-native-verify-completion",
    )
    namespace = _receipt_namespace(
        tmp_path / "compound-terminal-receipt",
        "factor-authority-compound-terminal-receipt",
    )
    latest_predecessor_ctime_ns = max(
        Path(item["path"]).stat().st_ctime_ns
        for item in (run_receipt, parent, evaluator, native)
    )
    with _real_root_lease_capability(
        monkeypatch,
        tmp_path / "lease",
        spec,
        state="verify_claimed",
    ) as (capability, epoch_files):
        result = contract.publish_compound_terminal_receipt(
            receipt_namespace=namespace,
            root_lease_capability=capability,
            run_spec=spec,
            compound_run_receipt_cas=run_receipt,
            parent_verifier_cas=parent,
            evaluator_verifier_cas=evaluator,
            native_verify_completion_cas=native,
        )
        payload = json.loads(Path(result["path"]).read_bytes())
        assert payload["bindings"] == {
            "compound_run_receipt_raw_sha256": run_receipt["raw_sha256"],
            "parent_verifier_raw_sha256": parent["raw_sha256"],
            "evaluator_verifier_raw_sha256": evaluator["raw_sha256"],
            "native_verify_completion_raw_sha256": native["raw_sha256"],
        }
        identity = spec["identity_binding"]
        assert payload["run_spec_sha256"] == spec["run_spec_sha256"]
        assert payload["attempt_key_sha256"] == identity["attempt_key_sha256"]
        assert payload["global_attempt_identity_sha256"] == identity[
            "global_attempt_identity_sha256"
        ]
        assert epoch_files["terminal.receipt.json"].is_file()
        assert Path(result["path"]).stat().st_ctime_ns >= latest_predecessor_ctime_ns
        assert (
            epoch_files["terminal.receipt.json"].stat().st_ctime_ns
            >= latest_predecessor_ctime_ns
        )
        assert not _contains_key(payload, "commit_order")


def test_terminal_epoch_comes_from_real_four_file_lease_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    identity = spec["identity_binding"]
    parameters = inspect.signature(contract.observe_compound_terminal_epoch).parameters
    assert set(parameters) == {
        "root_lease_capability",
        "run_spec_sha256",
        "attempt_key_sha256",
        "global_attempt_identity_sha256",
    }
    assert not {"state", "epoch", "commit_order"} & set(parameters)
    with _real_root_lease_capability(
        monkeypatch,
        tmp_path / "lease",
        spec,
        state="terminal",
    ) as (capability, epoch_files):
        assert set(path.name for path in epoch_files.values() if path.is_file()) == {
            "run.claim.json",
            "run.receipt.json",
            "verify.claim.json",
            "terminal.receipt.json",
        }
        assert root_lease.factor_v3_parent_source_lookup_root_epoch_transition(
            capability,
            run_spec_sha256=spec["run_spec_sha256"],
            attempt_key_sha256=identity["attempt_key_sha256"],
            global_attempt_identity_sha256=identity[
                "global_attempt_identity_sha256"
            ],
            requested_action=root_lease.PARENT_SOURCE_ROOT_ACTION_VERIFY,
        ) == (
            root_lease.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            root_lease.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
        )
        observation = contract.observe_compound_terminal_epoch(
            root_lease_capability=capability,
            run_spec_sha256=spec["run_spec_sha256"],
            attempt_key_sha256=identity["attempt_key_sha256"],
            global_attempt_identity_sha256=identity[
                "global_attempt_identity_sha256"
            ],
        )
        assert observation["root_state"] == root_lease.PARENT_SOURCE_ROOT_STATE_TERMINAL
        assert set(observation["epoch_file_raw_sha256"]) == set(epoch_files)


@pytest.mark.parametrize(
    "caller_claims",
    [
        {"state": "TERMINAL"},
        {"epoch": 4},
        {"commit_order": 999},
        {
            **{field: True for field in contract.AUTHORITY_TRUE_FIELDS},
            "native_runtime_authority_verified": True,
        },
    ],
    ids=("state", "epoch", "commit-order", "all-true-native-mapping"),
)
def test_caller_epoch_ordering_and_all_true_native_mappings_are_rejected(
    caller_claims: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    native = _write_auxiliary_cas(
        tmp_path / "native-authority",
        spec,
        category="factor-authority-native-terminal-authority",
    )
    with _real_root_lease_capability(
        monkeypatch,
        tmp_path / "lease",
        spec,
        state="terminal",
    ) as (capability, _epoch_files):
        with pytest.raises(
            contract.FactorAuthorityCompoundContractV2Error,
            match="caller.*state|caller.*epoch|caller.*order|caller.*authority",
        ):
            contract.authorize_compound_authority(
                root_lease_capability=capability,
                run_spec=spec,
                native_authority_cas=native,
                caller_claims=caller_claims,
            )


@pytest.mark.parametrize("scenario", ["disposable", "low-rvol"])
def test_disposable_and_low_rvol_fail_closed_guards_are_frozen(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    if scenario == "disposable":
        observation = contract.build_disposable_compound_observation(
            run_spec=spec,
            observed_evidence=[],
        )
        assert observation["test_fixture_only"] is True
        assert observation["disposable"] is True
        assert all(observation[field] is False for field in contract.AUTHORITY_TRUE_FIELDS)
        assert all(observation[field] is False for field in contract.SAFETY_FALSE_FIELDS)
        return
    evaluator = _write_role_cas(
        spec,
        "evaluator_producer",
        payload_extra={"selected_branch": branch_selector.LOW_RVOL_BRANCH},
    )
    with pytest.raises(
        contract.FactorAuthorityCompoundContractV2Error,
        match="low-rvol.*authority",
    ):
        contract.require_factor_v2_low_rvol_authority(
            evaluator_cas=evaluator,
            low_rvol_authority_cas=None,
        )


def test_publication_failure_leaves_success_namespace_category_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _run_spec_fixture(tmp_path, monkeypatch)
    roles = {
        role: _write_role_cas(spec, role) for role in contract.AUTHORITY_CAS_ROLES
    }
    run_receipt = _write_auxiliary_cas(
        tmp_path / "run-receipt",
        spec,
        category="factor-authority-compound-run-receipt",
    )
    terminal_receipt = _write_auxiliary_cas(
        tmp_path / "terminal-receipt",
        spec,
        category="factor-authority-compound-terminal-receipt",
    )
    native = _write_auxiliary_cas(
        tmp_path / "native-authority",
        spec,
        category="factor-authority-native-terminal-authority",
    )
    success_category = "factor-authority-compound-success"
    success_root = tmp_path / "success-cas"
    success_root.mkdir()
    success_namespace = {
        "canonical_root": str(success_root.resolve()),
        "expected_category": success_category,
    }

    def fail_before_success(*_args: object, **_kwargs: object) -> None:
        raise contract.FactorAuthorityCompoundPublicationFailure(
            "injected before success publication"
        )

    monkeypatch.setattr(contract, "_publish_success_cas", fail_before_success)
    with _real_root_lease_capability(
        monkeypatch,
        tmp_path / "lease",
        spec,
        state="terminal",
    ) as (capability, _epoch_files):
        with pytest.raises(
            contract.FactorAuthorityCompoundPublicationFailure,
            match="injected before success publication",
        ):
            contract.publish_compound_authority(
                success_namespace=success_namespace,
                run_spec=spec,
                root_lease_capability=capability,
                role_artifacts=roles,
                compound_run_receipt_cas=run_receipt,
                compound_terminal_receipt_cas=terminal_receipt,
                native_authority_cas=native,
            )
    success_category_root = success_root / success_category
    assert not success_category_root.exists() or not any(
        path.is_file() for path in success_category_root.rglob("*")
    )
