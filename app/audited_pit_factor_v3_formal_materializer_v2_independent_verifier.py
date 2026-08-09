"""Independent verifier process boundary for materializer v2 RED."""

from __future__ import annotations

from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_independent_core as core


assert authority is not None and core is not None


class FactorV3FormalMaterializerV2IndependentVerifierError(ValueError):
    """Raised by the verifier process's own physical/publication boundary."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(
        f"Factor V3 formal materializer v2 independent verifier RED: {capability}"
    )


def _independently_verify_disposable_materialization_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
    output_root: str,
) -> dict[str, Any]:
    """Publish a disposable-only receipt after the independent core replay."""

    _red("disposable independent verifier receipt")


def independently_verify_registered_formal_materialization_v2_once() -> dict[str, Any]:
    """Acquire one registered attempt from the native broker and replay it."""

    _red("registered independent verifier process")


__all__ = [
    "FactorV3FormalMaterializerV2IndependentVerifierError",
    "independently_verify_registered_formal_materialization_v2_once",
]
