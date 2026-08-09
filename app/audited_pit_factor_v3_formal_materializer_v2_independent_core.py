"""Independent raw replay core for Factor V3 materializer v2 RED.

This module imports only the data contract.  It does not import producer
validators, builders, I/O helpers, error classes, or publication code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract


class FactorV3FormalMaterializerV2IndependentCoreError(ValueError):
    """Raised by the independent implementation's own fail-closed path."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(
        f"Factor V3 formal materializer v2 independent core RED: {capability}"
    )


def independently_validate_calendar_v2(
    *,
    calendar_rows: Sequence[Mapping[str, Any]],
    parent_signal_dates: Sequence[str],
) -> dict[str, Any]:
    """Rebuild ordered 250/483/733/732 closure from raw session rows."""

    _red("independent strict calendar replay")


def independently_validate_board_ledger_v2(
    *,
    all_market_sessions: Sequence[str],
    source_dates: Sequence[str],
    upstream_board_rows: Sequence[Mapping[str, Any]],
    expected_per_date_prefilter_roots: Mapping[str, str],
) -> dict[str, Any]:
    """Rebuild all 733 per-date five-segment prefilter roots."""

    _red("independent board-ledger replay")


def independently_validate_parent_roots_v2(
    *,
    metadata: Mapping[str, Any],
    identity_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild all three parent projections and terminal/evaluator/cost roots."""

    _red("independent parent and evaluator root replay")


def independently_validate_sqlite_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
) -> dict[str, Any]:
    """Open read-only/no-follow and rebuild exact DDL and float64 byte roots."""

    _red("independent exact SQLite and float64 replay")


def independently_validate_arm_matrices_v2(
    *,
    metadata: Mapping[str, Any],
    identity_rows: Sequence[Mapping[str, Any]],
    arm_rows: Mapping[str, Sequence[bytes]],
) -> dict[str, Any]:
    """Rebuild rank[-1,1], breadth[0,1], and finite median 10/11/11 matrices."""

    _red("independent matrix replay")


def replay_disposable_materialization_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
) -> dict[str, Any]:
    """Replay a real disposable SQLite/receipt pair without formal promotion."""

    _red("independent disposable raw replay")


def replay_registered_formal_materialization_v2_once(
    *,
    registered_attempt_capability: object,
) -> dict[str, Any]:
    """Replay one registered formal attempt without caller paths or mappings."""

    _red("independent registered formal raw replay")


assert contract.INDEPENDENT_VERIFIER_ROLE != contract.PRODUCER_ROLE


__all__ = [
    "FactorV3FormalMaterializerV2IndependentCoreError",
    "independently_validate_arm_matrices_v2",
    "independently_validate_board_ledger_v2",
    "independently_validate_calendar_v2",
    "independently_validate_parent_roots_v2",
    "independently_validate_sqlite_v2",
    "replay_disposable_materialization_v2",
    "replay_registered_formal_materialization_v2_once",
]
