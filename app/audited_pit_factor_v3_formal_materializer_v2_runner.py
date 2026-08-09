"""Single-attempt native runner contract for Factor V3 materializer v2 RED.

The runner does not import producer or verifier Python modules.  A future
implementation may invoke only separately registered native programs through
the opaque broker session.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract


REGISTERED_PRODUCER_PROGRAM_NAME = (
    "app.audited_pit_factor_v3_formal_materializer_v2"
)
REGISTERED_VERIFIER_PROGRAM_NAME = (
    "app.audited_pit_factor_v3_formal_materializer_v2_independent_verifier"
)
DISPOSABLE_ATTEMPT_ROOT_NAMES = (
    "claim",
    "status",
    "artifact",
    "independent-verifier",
    "publication",
)


class FactorV3FormalMaterializerV2RunnerError(ValueError):
    """Raised when single-attempt state or root isolation fails closed."""


class FactorV3FormalMaterializerV2RunnerUnavailableError(
    FactorV3FormalMaterializerV2RunnerError
):
    """Raised before I/O while any native registration is absent."""


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(
        f"Factor V3 formal materializer v2 runner RED: {capability}"
    )


def _runner_io_probe(_operation: str, _path: str) -> None:
    return None


def run_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Claim and execute exactly one registered producer attempt."""

    _red("registered one-shot run")


def verify_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Execute exactly one registered independent verification attempt."""

    _red("registered one-shot verify")


def _claim_disposable_materialization_attempt_v2_once(
    *,
    semantic_input_root_sha256: str,
    claim_root: str,
    status_root: str,
    artifact_root: str,
    independent_verifier_root: str,
    publication_root: str,
) -> dict[str, Any]:
    """Exercise atomic create-only claim and pairwise-disjoint roots."""

    _red("disposable create-only attempt claim")


def _race_disposable_materialization_attempt_claims_v2(
    *,
    semantic_input_root_sha256: str,
    claim_root: str,
    status_root: str,
    artifact_root: str,
    independent_verifier_root: str,
    publication_root: str,
    contender_count: int,
) -> dict[str, Any]:
    """Prove one winner and immutable rejection for concurrent claims."""

    _red("disposable concurrent claim race")


def _mark_disposable_materialization_attempt_failed_terminal_v2(
    *,
    claim_path: str,
    status_root: str,
    reason_code: str,
) -> dict[str, Any]:
    """Create one terminal failure status that forbids resume and retry."""

    _red("disposable failure terminal state")


def main(argv: Sequence[str] | None = None) -> NoReturn:
    """Expose only ``run`` and ``verify``; both are single-attempt modes."""

    _red("single-attempt CLI")


assert contract.RUNNER_ROLE not in {
    contract.PRODUCER_ROLE,
    contract.INDEPENDENT_VERIFIER_ROLE,
}
assert authority is not None


if __name__ == "__main__":
    main()
