"""Pure RED seam for one compound parent/evaluator authority attempt.

The v2 contract deliberately has no GREEN implementation.  Identity is formed
only from pre-existing semantic input roots, and every production registry is
empty until an independently audited authority registration exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn


COMPOUND_SEMANTIC_IDENTITY_SCHEMA = "factor-authority-compound-semantic-identity/v2"
COMPOUND_RUN_SPEC_SCHEMA = "factor-authority-compound-run-spec/v2"
COMPOUND_CAS_DESCRIPTOR_SCHEMA = "factor-authority-compound-cas-descriptor/v2"
COMPOUND_ROLE_OUTPUT_SCHEMA = "factor-authority-compound-role-output/v2"
COMPOUND_RUN_RECEIPT_SCHEMA = "factor-authority-compound-run-receipt/v2"
COMPOUND_TERMINAL_RECEIPT_SCHEMA = "factor-authority-compound-terminal-receipt/v2"
COMPOUND_PUBLICATION_SCHEMA = "factor-authority-compound-publication/v2"
COMPOUND_DISPOSABLE_OBSERVATION_SCHEMA = (
    "factor-authority-compound-disposable-observation/v2"
)

PARENT_SOURCE_ROOT_LEASE_BINDING = {
    "module": "app.factor_v3_formal_trusted_supervisor",
    "attempt_entrypoint": "factor_v3_parent_source_derive_attempt_key_sha256",
    "global_identity_entrypoint": (
        "factor_v3_parent_source_derive_global_attempt_identity_sha256"
    ),
    "epoch_entrypoint": "factor_v3_parent_source_lookup_root_epoch_transition",
    "run_receipt_capability": "_ParentSourceRootLease.publish_run_receipt",
    "terminal_receipt_capability": "_ParentSourceRootLease.publish_terminal_receipt",
}

AUTHORITY_CAS_ROLES = (
    "parent_producer",
    "parent_verifier",
    "evaluator_producer",
    "evaluator_verifier",
)
EXPECTED_CATEGORY_BY_ROLE = {
    "parent_producer": "factor-v3-parent-source-producer",
    "parent_verifier": "factor-v3-parent-source-independent-verifier",
    "evaluator_producer": "factor-v2-terminal-evaluator-producer",
    "evaluator_verifier": "factor-v2-terminal-evaluator-independent-verifier",
}
AUTHORITY_TRUE_FIELDS = (
    "activation_verified",
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

# A later independently audited GREEN may replace these exact registrations.
REGISTERED_COMPOUND_NATIVE_TCB_SHA256: str | None = None
REGISTERED_PARENT_PRODUCER_SOURCE_AUTHORITY_SHA256: str | None = None
REGISTERED_PARENT_VERIFIER_SOURCE_AUTHORITY_SHA256: str | None = None
REGISTERED_EVALUATOR_PRODUCER_SOURCE_AUTHORITY_SHA256: str | None = None
REGISTERED_EVALUATOR_VERIFIER_SOURCE_AUTHORITY_SHA256: str | None = None


class FactorAuthorityCompoundContractV2Error(ValueError):
    """Raised when the v2 compound authority contract fails closed."""


class FactorAuthorityCompoundPublicationFailure(RuntimeError):
    """Raised by a test-only publication hook before the final success CAS."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(f"factor authority compound v2 RED: {capability}")


def _publish_success_cas(*_args: object, **_kwargs: object) -> NoReturn:
    _red("success CAS publication hook")


def build_compound_identity_binding(
    *,
    parent_semantic_input_root_sha256: str,
    evaluator_semantic_input_root_sha256: str,
) -> dict[str, Any]:
    """Bind only pre-existing semantic roots to the 52c root-lease identity."""

    _red("pre-existing semantic-to-attempt-to-global identity")


def build_compound_run_spec(
    *,
    identity_binding: Mapping[str, Any],
    cas_namespace_parent: str | Path,
    cas_namespaces: Mapping[str, Mapping[str, Any]],
    test_fixture_only: bool,
) -> dict[str, Any]:
    """Bind four empty namespaces and five registered authority hashes."""

    _red("registered empty-namespace run spec")


def validate_compound_role_cas(
    *,
    run_spec: Mapping[str, Any],
    role: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate physical path/raw/self/category/role and compound identity."""

    _red("physical role CAS validation")


def validate_shared_compound_attempt(
    *,
    run_spec: Mapping[str, Any],
    artifacts_by_role: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Require all four outputs to share run-spec, attempt, and global identity."""

    _red("shared four-role compound attempt")


def publish_compound_run_receipt(
    *,
    receipt_namespace: Mapping[str, Any],
    root_lease_capability: object,
    run_spec: Mapping[str, Any],
    parent_producer_cas: Mapping[str, Any],
    evaluator_producer_cas: Mapping[str, Any],
    native_run_completion_cas: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish after two producers and native completion through a live lease."""

    _red("producer/native-bound run receipt")


def publish_compound_terminal_receipt(
    *,
    receipt_namespace: Mapping[str, Any],
    root_lease_capability: object,
    run_spec: Mapping[str, Any],
    compound_run_receipt_cas: Mapping[str, Any],
    parent_verifier_cas: Mapping[str, Any],
    evaluator_verifier_cas: Mapping[str, Any],
    native_verify_completion_cas: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish after run receipt, two verifiers, and native verify completion."""

    _red("verifier/native-bound terminal receipt")


def observe_compound_terminal_epoch(
    *,
    root_lease_capability: object,
    run_spec_sha256: str,
    attempt_key_sha256: str,
    global_attempt_identity_sha256: str,
) -> dict[str, Any]:
    """Observe TERMINAL only through the live root-lease capability."""

    _red("capability-derived four-file terminal epoch")


def authorize_compound_authority(
    *,
    root_lease_capability: object,
    run_spec: Mapping[str, Any],
    native_authority_cas: Mapping[str, Any],
    caller_claims: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject caller state, epoch, ordering, and all-true/native mappings."""

    _red("caller authority mapping rejection")


def build_disposable_compound_observation(
    *,
    run_spec: Mapping[str, Any],
    observed_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Emit a disposable observation with every authority/safety gate false."""

    _red("disposable false-gate observation")


def require_factor_v2_low_rvol_authority(
    *,
    evaluator_cas: Mapping[str, Any],
    low_rvol_authority_cas: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Require a dedicated authority for the selected low-rvol fallback."""

    _red("Factor V2 low-rvol authority")


def publish_compound_authority(
    *,
    success_namespace: Mapping[str, Any],
    run_spec: Mapping[str, Any],
    root_lease_capability: object,
    role_artifacts: Mapping[str, Mapping[str, Any]],
    compound_run_receipt_cas: Mapping[str, Any],
    compound_terminal_receipt_cas: Mapping[str, Any],
    native_authority_cas: Mapping[str, Any],
) -> dict[str, Any]:
    """Postverify every predecessor before the final success-category CAS."""

    _red("publication-last success CAS")
