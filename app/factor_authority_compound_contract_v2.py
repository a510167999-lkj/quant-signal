"""Versioned RED contract for one compound parent/evaluator authority attempt.

This module intentionally contains no authority implementation.  The public
entry points define the v2 seam while every production registry remains empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn


COMPOUND_SEMANTIC_IDENTITY_SCHEMA = "factor-authority-compound-semantic-identity/v2"
COMPOUND_ATTEMPT_KEY_SCHEMA = "factor-authority-compound-attempt-key/v2"
COMPOUND_RUN_SPEC_SCHEMA = "factor-authority-compound-run-spec/v2"
COMPOUND_CAS_DESCRIPTOR_SCHEMA = "factor-authority-compound-cas-descriptor/v2"
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
    "epoch_lookup_entrypoint": (
        "factor_v3_parent_source_lookup_root_epoch_transition"
    ),
}

AUTHORITY_CAS_ROLES = (
    "parent_producer",
    "parent_verifier",
    "evaluator_producer",
    "evaluator_verifier",
)
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

# A later independently audited GREEN must register immutable production roots.
REGISTERED_COMPOUND_NATIVE_TCB_SHA256: str | None = None
REGISTERED_PARENT_PRODUCER_AUTHORITY_SHA256: str | None = None
REGISTERED_PARENT_VERIFIER_AUTHORITY_SHA256: str | None = None
REGISTERED_EVALUATOR_PRODUCER_AUTHORITY_SHA256: str | None = None
REGISTERED_EVALUATOR_VERIFIER_AUTHORITY_SHA256: str | None = None


class FactorAuthorityCompoundContractV2Error(ValueError):
    """Raised when the v2 compound authority contract must fail closed."""


class FactorAuthorityCompoundPublicationFailure(RuntimeError):
    """Raised by a test-only fault before the final success publication."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(f"factor authority compound v2 RED: {capability}")


def build_compound_identity_binding(
    *,
    parent_producer_root_sha256: str,
    parent_verifier_root_sha256: str,
    evaluator_producer_root_sha256: str,
    evaluator_verifier_root_sha256: str,
) -> dict[str, Any]:
    """Bind semantic input to the existing root-lease attempt/global identity."""

    _red("semantic-to-attempt-to-global identity")


def build_compound_run_spec(
    *,
    identity_binding: Mapping[str, Any],
    authority_cas: Mapping[str, Mapping[str, Any]],
    global_attempt_ledger_root: str | Path,
) -> dict[str, Any]:
    """Bind four disjoint physical CAS roots into one compound attempt."""

    _red("four-CAS run spec")


def validate_compound_terminal_epoch(
    *,
    run_spec: Mapping[str, Any],
    epoch_observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject a terminal epoch unless all four physical CAS objects exist."""

    _red("terminal epoch CAS closure")


def publish_compound_run_receipt(
    *,
    destination: str | Path,
    run_spec: Mapping[str, Any],
    parent_producer_cas: Mapping[str, Any],
    evaluator_producer_cas: Mapping[str, Any],
    native_run_completion: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish run.receipt only after both producers and native completion."""

    _red("run receipt publication ordering")


def publish_compound_terminal_receipt(
    *,
    destination: str | Path,
    run_receipt: Mapping[str, Any],
    parent_verifier_cas: Mapping[str, Any],
    evaluator_verifier_cas: Mapping[str, Any],
    native_verify_completion: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish terminal.receipt only after both verifiers and verify completion."""

    _red("terminal receipt publication ordering")


def validate_physical_cas_artifact(
    *,
    descriptor: Mapping[str, Any],
    expected_category: str,
    allowed_root: str | Path,
) -> dict[str, Any]:
    """Validate path, raw bytes, self hash, and category on one CAS object."""

    _red("physical CAS validation")


def validate_shared_compound_attempt(
    *,
    run_spec: Mapping[str, Any],
    parent_evidence: Mapping[str, Any],
    evaluator_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Require parent and evaluator evidence to share one compound attempt."""

    _red("shared parent/evaluator attempt")


def authorize_compound_authority(
    *,
    run_spec: Mapping[str, Any],
    caller_claims: Mapping[str, Any],
    native_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive authority from registered native evidence, never caller booleans."""

    _red("registered native authority derivation")


def build_disposable_compound_observation(
    *,
    run_spec: Mapping[str, Any],
    observed_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Emit an explicitly disposable observation with every authority gate false."""

    _red("disposable false-gate observation")


def require_factor_v2_low_rvol_authority(
    *,
    evaluator_cas: Mapping[str, Any],
    low_rvol_authority_cas: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Require dedicated authority for the selected Factor V2 low-rvol branch."""

    _red("Factor V2 low-rvol authority")


def publish_compound_authority(
    *,
    output_root: str | Path,
    run_spec: Mapping[str, Any],
    native_result: Mapping[str, Any],
    test_fixture_only: bool = False,
    _test_failure_stage: str | None = None,
) -> dict[str, Any]:
    """Publish success last; production remains unavailable while registry is empty."""

    _red("publication-last compound authority")
