"""Pure RED contract for one compound parent/evaluator authority attempt.

No production authority is registered.  Semantic identity binds only inputs
that already exist, while the run specification and every later object remain
opaque, physically held capabilities issued by the compiled native broker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from app import factor_authority_compound_native_client as native_client


COMPOUND_SEMANTIC_IDENTITY_SCHEMA = "factor-authority-compound-semantic-identity/v2"
COMPOUND_DEPLOYMENT_POLICY_SCHEMA = "factor-authority-compound-deployment-policy/v2"
COMPOUND_RUN_SPEC_SCHEMA = "factor-authority-compound-run-spec/v2"
COMPOUND_CAS_DESCRIPTOR_SCHEMA = "factor-authority-compound-cas-descriptor/v2"
COMPOUND_ROLE_OUTPUT_SCHEMA = "factor-authority-compound-role-output/v2"
COMPOUND_RUN_RECEIPT_SCHEMA = "factor-authority-compound-run-receipt/v2"
COMPOUND_TERMINAL_RECEIPT_SCHEMA = "factor-authority-compound-terminal-receipt/v2"
COMPOUND_NATIVE_RUN_SCHEMA = "factor-authority-compound-native-run-closure/v2"
COMPOUND_NATIVE_VERIFY_SCHEMA = "factor-authority-compound-native-verify-closure/v2"
COMPOUND_NATIVE_TERMINAL_SCHEMA = "factor-authority-compound-native-terminal/v2"
COMPOUND_LOW_RVOL_AUTHORITY_SCHEMA = "factor-authority-compound-low-rvol/v2"
COMPOUND_PUBLICATION_SCHEMA = "factor-authority-compound-publication/v2"
COMPOUND_DISPOSABLE_OBSERVATION_SCHEMA = (
    "factor-authority-compound-disposable-observation/v2"
)

COMPOUND_NATIVE_ROOT_LEASE_BINDING = {
    "module": "app.factor_authority_compound_native_client",
    "manifest_schema": native_client.COMPOUND_NATIVE_MANIFEST_SCHEMA,
    "capability": "HeldCompoundRootLease",
    "acquire_entrypoint": "acquire_compound_root_lease",
    "transition_entrypoint": "transition_compound_root_epoch",
    "postverify_entrypoint": "postverify_compound_root_lease",
}

ROLE_OUTPUT_NAMES = (
    "parent_producer",
    "parent_verifier",
    "evaluator_producer",
    "evaluator_verifier",
)
PRODUCER_ROLE_NAMES = ("parent_producer", "evaluator_producer")
VERIFIER_ROLE_NAMES = ("parent_verifier", "evaluator_verifier")
DEPLOYMENT_NAMESPACE_NAMES = (
    "global_attempt_ledger",
    *ROLE_OUTPUT_NAMES,
    "compound_run_receipt",
    "compound_terminal_receipt",
    "native_run_completion",
    "native_verify_completion",
    "native_terminal_authority",
    "success",
)
PREEXISTING_AUTHORITY_NAMES = (
    "factor_v2_terminal_decision_authority",
    "factor_v2_low_rvol_branch_authority",
)
EXPECTED_CATEGORY_BY_NAMESPACE = {
    "global_attempt_ledger": "factor-authority-root-epoch",
    "parent_producer": "factor-v3-parent-source-producer",
    "parent_verifier": "factor-v3-parent-source-independent-verifier",
    "evaluator_producer": "factor-v2-terminal-evaluator-producer",
    "evaluator_verifier": "factor-v2-terminal-evaluator-independent-verifier",
    "compound_run_receipt": "factor-authority-compound-run-receipt",
    "compound_terminal_receipt": "factor-authority-compound-terminal-receipt",
    "native_run_completion": "factor-authority-native-run-completion",
    "native_verify_completion": "factor-authority-native-verify-completion",
    "native_terminal_authority": "factor-authority-native-terminal-authority",
    "success": "factor-authority-compound-success",
}
EXPECTED_CATEGORY_BY_PREEXISTING_AUTHORITY = {
    "factor_v2_terminal_decision_authority": (
        "factor-v2-terminal-decision-authority"
    ),
    "factor_v2_low_rvol_branch_authority": "factor-v2-low-rvol-branch-authority",
}

RUN_RECEIPT_RAW_BINDINGS = (
    "root_run_claim_raw_sha256",
    "parent_producer_raw_sha256",
    "evaluator_producer_raw_sha256",
    "native_run_completion_raw_sha256",
)
TERMINAL_RECEIPT_RAW_BINDINGS = (
    "compound_run_receipt_raw_sha256",
    "root_run_receipt_raw_sha256",
    "root_verify_claim_raw_sha256",
    "parent_verifier_raw_sha256",
    "evaluator_verifier_raw_sha256",
    "native_verify_completion_raw_sha256",
)
NATIVE_RUN_RAW_BINDINGS = (
    "root_run_claim_raw_sha256",
    "parent_producer_raw_sha256",
    "evaluator_producer_raw_sha256",
)
NATIVE_VERIFY_RAW_BINDINGS = (
    "root_run_receipt_raw_sha256",
    "root_verify_claim_raw_sha256",
    "compound_run_receipt_raw_sha256",
    "parent_verifier_raw_sha256",
    "evaluator_verifier_raw_sha256",
)
NATIVE_TERMINAL_RAW_BINDINGS = (
    "root_run_claim_raw_sha256",
    "root_run_receipt_raw_sha256",
    "root_verify_claim_raw_sha256",
    "root_terminal_receipt_raw_sha256",
    *(f"{role}_raw_sha256" for role in ROLE_OUTPUT_NAMES),
    "compound_run_receipt_raw_sha256",
    "compound_terminal_receipt_raw_sha256",
    "native_run_completion_raw_sha256",
    "native_verify_completion_raw_sha256",
    "program_set_root_sha256",
    "attempt_key_sha256",
    "global_attempt_identity_sha256",
    "run_spec_raw_sha256",
)

AUTHORITY_TRUE_FIELDS = (
    "compound_authority_verified",
    "formal_materialization_eligible",
    "parent_source_authority_verified",
    "source_authority_complete",
    "terminal_evaluator_authority_verified",
    "verified",
)
SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "final_oos_consumed",
    "model_training_started",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
)

REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH: str | None = None
REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_RAW_SHA256: str | None = None
HANDOFF_READY = 0


class FactorAuthorityCompoundContractV2Error(ValueError):
    """Raised when the v2 compound authority contract fails closed."""


class FactorAuthorityCompoundPublicationFailure(RuntimeError):
    """Raised by an injected publication probe before success CAS creation."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(f"factor authority compound v2 RED: {capability}")


def _held_cas_read_probe(_stage: str, _path: str) -> None:
    return None


def _publication_probe(_stage: str, _held: Sequence[object]) -> None:
    return None


def _publish_success_cas(*_args: object, **_kwargs: object) -> NoReturn:
    _red("success CAS publication hook")


def build_compound_identity_binding(
    *,
    program_set_root_sha256: str,
    parent_semantic_input_root_sha256: str,
    evaluator_semantic_input_root_sha256: str,
    factor_v2_terminal_decision_authority_root_sha256: str,
    factor_v2_low_rvol_branch_authority_root_sha256: str,
) -> dict[str, Any]:
    """Bind only pre-existing program, semantic, decision, and branch roots."""

    _red("pre-existing program/semantic/decision/branch identity")


def open_registered_compound_deployment_policy_authority(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
) -> native_client.HeldDeploymentPolicyAuthority:
    """Open the registered signed policy through a live compiled session."""

    _red("physical signed deployment-policy authority")


def build_compound_run_spec(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    identity_binding: Mapping[str, Any],
    deployment_policy_authority: native_client.HeldDeploymentPolicyAuthority,
) -> native_client.HeldCompoundRunSpec:
    """Issue a held policy-bound run spec before producing any output."""

    _red("opaque registered deployment-policy run spec")


def open_held_compound_cas(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    namespace_name: str,
    expected_raw_sha256: str,
) -> native_client.HeldRegisteredCas:
    """Open no-follow and retain the file plus ancestors through publication."""

    _red("same-handle ancestor-held CAS ownership")


def postverify_held_compound_cas(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    held_cas: native_client.HeldRegisteredCas,
) -> dict[str, Any]:
    """Recheck ID, nlink, size, roots, owner-DACL, namespace, and handle."""

    _red("held CAS postverification")


def open_held_preexisting_authority(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    authority_name: str,
    expected_raw_sha256: str,
) -> native_client.HeldRegisteredCas:
    """Hold one policy-registered authority that pre-dates the run spec."""

    _red("held pre-existing authority input")


def validate_shared_compound_attempt(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    run_spec: native_client.HeldCompoundRunSpec,
    role_artifacts: Mapping[str, native_client.HeldRegisteredCas],
    role_productions: Mapping[str, native_client.HeldRoleProduction],
) -> dict[str, Any]:
    """Require four broker-produced artifacts to share one opaque attempt."""

    _red("shared opaque four-role compound attempt")


def validate_native_run_exact_closure(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    parent_producer_cas: native_client.HeldRegisteredCas,
    evaluator_producer_cas: native_client.HeldRegisteredCas,
    parent_producer_production: native_client.HeldRoleProduction,
    evaluator_producer_production: native_client.HeldRoleProduction,
    native_run_completion: native_client.HeldNativeCompletion,
) -> dict[str, Any]:
    """Bind run.claim raw plus exactly two broker producer raw hashes."""

    _red("native run exact raw closure")


def validate_native_verify_exact_closure(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    compound_run_receipt_cas: native_client.HeldRegisteredCas,
    parent_verifier_cas: native_client.HeldRegisteredCas,
    evaluator_verifier_cas: native_client.HeldRegisteredCas,
    parent_verifier_production: native_client.HeldRoleProduction,
    evaluator_verifier_production: native_client.HeldRoleProduction,
    native_verify_completion: native_client.HeldNativeCompletion,
) -> dict[str, Any]:
    """Bind run/verify epoch, compound run, and exactly two verifier raws."""

    _red("native verify exact raw closure")


def validate_native_terminal_exact_closure(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    role_artifacts: Mapping[str, native_client.HeldRegisteredCas],
    role_productions: Mapping[str, native_client.HeldRoleProduction],
    compound_run_receipt_cas: native_client.HeldRegisteredCas,
    compound_terminal_receipt_cas: native_client.HeldRegisteredCas,
    native_run_completion: native_client.HeldNativeCompletion,
    native_verify_completion: native_client.HeldNativeCompletion,
    native_terminal_authority: native_client.HeldNativeCompletion,
) -> dict[str, Any]:
    """Bind four epochs, four roles, receipts, completions, and identity roots."""

    _red("native terminal exact raw closure")


def start_compound_run_with_low_rvol_gate(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    factor_v2_terminal_decision_authority_cas: native_client.HeldRegisteredCas,
    low_rvol_branch_authority_cas: native_client.HeldRegisteredCas | None,
) -> dict[str, Any]:
    """Require related pre-existing decision/branch CAS before START_RUN."""

    _red("pre-START_RUN low-rvol physical authority")


def publish_compound_run_receipt(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    parent_producer_cas: native_client.HeldRegisteredCas,
    evaluator_producer_cas: native_client.HeldRegisteredCas,
    parent_producer_production: native_client.HeldRoleProduction,
    evaluator_producer_production: native_client.HeldRoleProduction,
    native_run_completion: native_client.HeldNativeCompletion,
) -> native_client.HeldRegisteredCas:
    """Publish only the exact run claim, producer, and native-run closure."""

    _red("registered compound run receipt exact binding")


def publish_compound_terminal_receipt(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    compound_run_receipt_cas: native_client.HeldRegisteredCas,
    parent_verifier_cas: native_client.HeldRegisteredCas,
    evaluator_verifier_cas: native_client.HeldRegisteredCas,
    parent_verifier_production: native_client.HeldRoleProduction,
    evaluator_verifier_production: native_client.HeldRoleProduction,
    native_verify_completion: native_client.HeldNativeCompletion,
) -> native_client.HeldRegisteredCas:
    """Publish only the run receipt, epochs, verifier, and native-verify closure."""

    _red("registered compound terminal receipt exact binding")


def observe_compound_terminal_epoch(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
) -> dict[str, Any]:
    """Observe all four epoch files only through the unchanged live lease."""

    _red("capability-derived four-file terminal epoch")


def authorize_compound_authority(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    role_artifacts: Mapping[str, native_client.HeldRegisteredCas],
    role_productions: Mapping[str, native_client.HeldRoleProduction],
    compound_run_receipt_cas: native_client.HeldRegisteredCas,
    compound_terminal_receipt_cas: native_client.HeldRegisteredCas,
    native_run_completion: native_client.HeldNativeCompletion,
    native_verify_completion: native_client.HeldNativeCompletion,
    native_terminal_authority: native_client.HeldNativeCompletion,
) -> dict[str, Any]:
    """Authorize from the complete v2 closure, never a terminal object alone."""

    _red("registered native terminal exact authority")


def _build_disposable_compound_observation(
    *,
    run_spec: native_client.HeldCompoundRunSpec,
    observed_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Private false-only observation that cannot call the success hook."""

    _red("private disposable false-gate observation")


def publish_compound_authority(
    *,
    native_session_set: native_client.HeldCompoundNativeSessionSet,
    root_lease_capability: native_client.HeldCompoundRootLease,
    run_spec: native_client.HeldCompoundRunSpec,
    role_artifacts: Mapping[str, native_client.HeldRegisteredCas],
    role_productions: Mapping[str, native_client.HeldRoleProduction],
    compound_run_receipt_cas: native_client.HeldRegisteredCas,
    compound_terminal_receipt_cas: native_client.HeldRegisteredCas,
    native_run_completion: native_client.HeldNativeCompletion,
    native_verify_completion: native_client.HeldNativeCompletion,
    native_terminal_authority: native_client.HeldNativeCompletion,
) -> native_client.HeldRegisteredCas:
    """Postverify every held predecessor before the final success CAS hook."""

    _red("publication-last held exact closure")
