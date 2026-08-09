"""Pure RED producer for Factor V3 formal-development materialization v2.

Formal production accepts only opaque capabilities from the separately frozen
authority module.  Private disposable seams exist solely to freeze physical
and matrix contracts; they can never emit a formal or verified receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority


class FactorV3FormalMaterializerV2Error(ValueError):
    """Raised when producer evidence violates the v2 contract."""


class FactorV3FormalMaterializerV2PublicationFailure(RuntimeError):
    """Raised after complete replay but before final disposable publication."""


class HeldDisposableMaterializationInputsV2:
    """Opaque no-follow holder for disposable physical contract tests."""

    __slots__ = ("_held_ancestors", "_held_files")

    def __new__(cls) -> HeldDisposableMaterializationInputsV2:
        raise TypeError("disposable held inputs are opener-only")


def _red(capability: str) -> NoReturn:
    raise FactorV3FormalMaterializerV2Error(
        f"Factor V3 formal materializer v2 unavailable: {capability}"
    )


def _held_input_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _publication_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _materialization_stage_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _producer_io_probe(_operation: str, _path: str) -> None:
    return None


def _is_reparse_point(_path: str) -> bool:
    return False


def reject_unregistered_activation_input_v2(
    *,
    untrusted_schema: str,
    untrusted_authority_scope: str,
) -> NoReturn:
    """Reject v1, candidates, disposable receipts, and caller authority."""

    from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract

    formal_ok = (
        untrusted_schema == contract.FORMAL_ACTIVATION_INDEPENDENT_RECEIPT_SCHEMA
        and untrusted_authority_scope == contract.FORMAL_AUTHORITY_SCOPE
    )
    if not formal_ok:
        raise FactorV3FormalMaterializerV2Error(
            "unregistered activation input rejected: "
            f"schema={untrusted_schema!r} scope={untrusted_authority_scope!r}"
        )
    raise FactorV3FormalMaterializerV2Error(
        "activation input rejected without opaque registered capability"
    )


def validate_exact_calendar_projection_v2(
    *,
    calendar_projection: Mapping[str, Any],
    parent_signal_dates: Sequence[str],
) -> dict[str, Any]:
    """Validate strict dates, hashes, 250+483, 733, all[:-1], and parent dates."""

    _red("exact ordered 250/483/733/732 calendar closure")


def validate_parent_frozen_projection_v2(
    *,
    parent_projection: Mapping[str, Any],
    activation_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind all parent, projection, terminal, evaluator, and cost roots."""

    _red("frozen parent and terminal evaluator root closure")


def project_formal_market_scope_v2(
    *,
    all_market_sessions: Sequence[str],
    source_dates: Sequence[str],
    upstream_board_rows: Sequence[Mapping[str, Any]],
    expected_per_date_prefilter_roots: Mapping[str, str],
    parent_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate five segments per date before downstream BSE/STAR filtering."""

    _red("733-date upstream board ledger and 732-date source binding")


def derive_turnover_features_v2(
    *,
    t_minus_one_turnover_rate_f: float,
    observed_turnover_rate_f_20: Sequence[float],
    observed_turnover_rate_f_250: Sequence[float],
) -> dict[str, float]:
    """Keep finite zero observations and reject non-finite or undefined ratios."""

    _red("zero-preserving finite turnover feature semantics")


def reconcile_exclusion_ledger_v2(
    *,
    parent_identity_rows: Sequence[Mapping[str, Any]],
    eligible_identity_rows: Sequence[Mapping[str, Any]],
    exclusion_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require content-addressed, preregistered, per-date exact reconciliation."""

    _red("content-addressed preregistered exclusion ledger")


def build_factor_v3_arm_matrices_v2(
    *,
    development_sessions: Sequence[str],
    previous_session_by_signal_date: Mapping[str, str],
    base_feature_rows: Sequence[Mapping[str, Any]],
    factor_source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build finite [0,1] 10/11/11 matrices with exact T-1 alignment."""

    _red("three-arm T-1 identical-identity float64 matrices")


def validate_materialization_safety_state_v2(
    *,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep training and every later lifecycle state false."""

    _red("post-materialization lifecycle isolation")


def _open_disposable_held_input_set_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
    independent_receipt_path: str,
    expected_independent_receipt_file_sha256: str,
    output_root: str,
) -> HeldDisposableMaterializationInputsV2:
    """Hold valid disposable predecessors without formal promotion."""

    _red("disposable same-handle file and ancestor ownership")


def _postverify_disposable_held_input_set_v2(
    *,
    held: HeldDisposableMaterializationInputsV2,
) -> dict[str, Any]:
    """Reject reparse, hardlink, ABA, DACL, ancestor, raw, or staging drift."""

    _red("disposable held-input physical postverification")


def _publish_disposable_materialization_contract_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
    independent_receipt_path: str,
    expected_independent_receipt_file_sha256: str,
    output_root: str,
) -> dict[str, Any]:
    """Exercise publication-last with formal and lifecycle gates false."""

    _red("disposable publication-last transaction")


def produce_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Acquire and consume one unswappable registered native capability."""

    if (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    ):
        raise authority.FactorV3FormalMaterializerV2UnavailableError(
            "registered materializer v2 native TCB/activation authority is unavailable"
        )
    _red("registered opaque single-attempt producer")


assert authority is not None


__all__ = [
    "FactorV3FormalMaterializerV2Error",
    "FactorV3FormalMaterializerV2PublicationFailure",
    "HeldDisposableMaterializationInputsV2",
    "build_factor_v3_arm_matrices_v2",
    "derive_turnover_features_v2",
    "produce_registered_factor_v3_formal_materialization_v2_once",
    "project_formal_market_scope_v2",
    "reconcile_exclusion_ledger_v2",
    "reject_unregistered_activation_input_v2",
    "validate_exact_calendar_projection_v2",
    "validate_materialization_safety_state_v2",
    "validate_parent_frozen_projection_v2",
]
