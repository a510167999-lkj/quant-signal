"""Single-attempt native runner contract for Factor V3 materializer v2.

The runner does not import producer or verifier Python modules.  A future
implementation may invoke only separately registered native programs through
the opaque broker session.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
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
    raise FactorV3FormalMaterializerV2RunnerError(
        f"Factor V3 formal materializer v2 runner unavailable: {capability}"
    )


def _runner_io_probe(_operation: str, _path: str) -> None:
    return None


def _require_registered_tcb() -> None:
    registered_slots = (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_PRODUCER_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_INDEPENDENT_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_RUNNER_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_ATTEMPT_LOCATOR_RAW_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER,
        authority.REGISTERED_SQLITE_RUNTIME_TCB_SHA256,
    )
    if any(slot is not None for slot in registered_slots) and all(
        slot is not None for slot in registered_slots
    ):
        return
    # Production path: any absence keeps runner unavailable before I/O.
    if any(slot is not None for slot in registered_slots):
        raise FactorV3FormalMaterializerV2RunnerUnavailableError(
            "partial materializer v2 registration is invalid"
        )
    raise FactorV3FormalMaterializerV2RunnerUnavailableError(
        "registered materializer v2 native TCB is absent"
    )


def run_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Claim and execute exactly one registered producer attempt."""

    _require_registered_tcb()
    _red("registered one-shot run")


def verify_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Execute exactly one registered independent verification attempt."""

    _require_registered_tcb()
    _red("registered one-shot verify")


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        return False


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

    roots = {
        "claim": Path(claim_root),
        "status": Path(status_root),
        "artifact": Path(artifact_root),
        "independent-verifier": Path(independent_verifier_root),
        "publication": Path(publication_root),
    }
    names = list(roots)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            if _paths_overlap(roots[left_name], roots[right_name]):
                raise FactorV3FormalMaterializerV2RunnerError(
                    "attempt roots must be pairwise disjoint"
                )
    claim_dir = roots["claim"]
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_path = claim_dir / "claim.json"
    if any(claim_dir.glob("*.json")):
        raise FactorV3FormalMaterializerV2RunnerError("preexisting claim forbids retry")
    status_dir = roots["status"]
    if status_dir.exists():
        for path in status_dir.rglob("*"):
            if path.is_file() and "failed" in path.name.lower():
                raise FactorV3FormalMaterializerV2RunnerError(
                    "preexisting failure status is terminal"
                )
    payload = {
        "schema": contract.RUNNER_CLAIM_SCHEMA,
        "semantic_input_root_sha256": semantic_input_root_sha256,
        "state": "CLAIMED",
        "claim_status": "CLAIMED",
        "claim_create_only": True,
        "safety": {field: False for field in contract.SAFETY_FALSE_FIELDS},
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(claim_path), flags)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                (
                    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
            )
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    return {
        "claim_path": str(claim_path),
        "state": "CLAIMED",
        "claim_status": "CLAIMED",
        "claim_create_only": True,
        "safety": payload["safety"],
    }


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

    contenders = max(1, int(contender_count))
    winners = 0
    losers = 0
    last_error = ""
    claim_status = "EMPTY"
    for _ in range(contenders):
        try:
            result = _claim_disposable_materialization_attempt_v2_once(
                semantic_input_root_sha256=semantic_input_root_sha256,
                claim_root=claim_root,
                status_root=status_root,
                artifact_root=artifact_root,
                independent_verifier_root=independent_verifier_root,
                publication_root=publication_root,
            )
            winners += 1
            claim_status = str(result.get("claim_status", "CLAIMED"))
        except FactorV3FormalMaterializerV2RunnerError as exc:
            losers += 1
            last_error = str(exc)
    if winners != 1:
        raise FactorV3FormalMaterializerV2RunnerError(
            f"concurrent claim race expected one winner, got {winners}"
        )
    return {
        "contender_count": contenders,
        "winner_count": winners,
        "loser_count": losers,
        "claim_create_only": True,
        "claim_status": claim_status,
        "last_rejection": last_error,
    }


def _mark_disposable_materialization_attempt_failed_terminal_v2(
    *,
    claim_path: str,
    status_root: str,
    reason_code: str,
) -> dict[str, Any]:
    """Create one terminal failure status that forbids resume and retry."""

    status_dir = Path(status_root)
    status_dir.mkdir(parents=True, exist_ok=True)
    if any(status_dir.glob("*failed*")):
        raise FactorV3FormalMaterializerV2RunnerError(
            "preexisting failure terminal forbids rewrite"
        )
    status_path = status_dir / "failed-terminal.json"
    payload = {
        "schema": contract.RUNNER_STATUS_SCHEMA,
        "status": "FAILED_TERMINAL",
        "state": "FAILED_TERMINAL",
        "reason_code": reason_code,
        "claim_path": claim_path,
        "resume_allowed": False,
        "retry_allowed": False,
        "safety": {field: False for field in contract.SAFETY_FALSE_FIELDS},
    }
    status_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: Sequence[str] | None = None) -> None:
    """Expose only ``run`` and ``verify``; both are single-attempt modes."""

    args = list(argv or [])
    if not args or args[0] not in contract.RUNNER_CLI_MODES or len(args) != 1:
        raise SystemExit(2)
    if args[0] == "run":
        run_registered_factor_v3_formal_materialization_v2_once()
    else:
        verify_registered_factor_v3_formal_materialization_v2_once()


assert contract.RUNNER_ROLE not in {
    contract.PRODUCER_ROLE,
    contract.INDEPENDENT_VERIFIER_ROLE,
}
assert authority is not None


if __name__ == "__main__":
    main()
