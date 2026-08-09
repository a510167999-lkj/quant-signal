"""Independent verifier process boundary for materializer v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_independent_core as core


assert authority is not None and core is not None


class FactorV3FormalMaterializerV2IndependentVerifierError(ValueError):
    """Raised by the verifier process's own physical/publication boundary."""


def _red(capability: str) -> NoReturn:
    raise FactorV3FormalMaterializerV2IndependentVerifierError(
        f"Factor V3 formal materializer v2 independent verifier unavailable: {capability}"
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

    try:
        result = core.replay_disposable_materialization_v2(
            sqlite_artifact_path=sqlite_artifact_path,
            expected_sqlite_artifact_file_sha256=expected_sqlite_artifact_file_sha256,
            producer_receipt_path=producer_receipt_path,
            expected_producer_receipt_file_sha256=expected_producer_receipt_file_sha256,
        )
    except core.FactorV3FormalMaterializerV2IndependentCoreError as exc:
        raise FactorV3FormalMaterializerV2IndependentVerifierError(str(exc)) from exc
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    return {
        **result,
        "structural_replay_verified": True,
        "output_root": str(output),
    }


def independently_verify_registered_formal_materialization_v2_once() -> dict[str, Any]:
    """Acquire one registered attempt from the native broker and replay it."""

    if (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    ):
        raise authority.FactorV3FormalMaterializerV2UnavailableError(
            "registered independent verifier TCB is unavailable"
        )
    _red("registered independent verifier process")


__all__ = [
    "FactorV3FormalMaterializerV2IndependentVerifierError",
    "independently_verify_registered_formal_materialization_v2_once",
]
