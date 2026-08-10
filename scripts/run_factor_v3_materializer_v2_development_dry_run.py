#!/usr/bin/env python3
"""Development-only materializer v2 dry-run with durable evidence.

Gates:
  - VPS_RUNTIME_ROLE=local_research (required)
  - process-local development registration fixture (install/uninstall)
  - disposable sqlite/receipt/replay/claim path only
  - formal produce/runner remain RED after fixture install (expected)

Never registers production profile, never opens embargo/final-OOS, never trades.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from app import audited_pit_factor_v3_formal_materializer_v2 as producer  # noqa: E402
from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority  # noqa: E402
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract  # noqa: E402
from app import audited_pit_factor_v3_formal_materializer_v2_independent_core as core  # noqa: E402
from app import audited_pit_factor_v3_formal_materializer_v2_runner as runner  # noqa: E402
from app import factor_v3_materializer_v2_development_registration_fixture as mat_fixture  # noqa: E402
from app import research_goal_contract as goal  # noqa: E402
from _factor_v3_formal_materializer_v2_disposable import (  # noqa: E402
    create_disposable_materialization_fixture,
)

DRY_RUN_SCHEMA = "factor-v3-materializer-v2-development-dry-run-evidence/v1"
REQUIRED_ROLE = "local_research"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_ROLE:
        raise SystemExit(
            f"refuse dry-run: set VPS_RUNTIME_ROLE={REQUIRED_ROLE} "
            f"(got {role!r})"
        )
    return role.strip()


def _expect_raises(label: str, fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # intentional: evidence of RED gates
        return {
            "label": label,
            "raised": True,
            "exception_type": type(exc).__name__,
            "message": str(exc)[:500],
        }
    return {
        "label": label,
        "raised": False,
        "exception_type": None,
        "message": "expected exception was not raised",
    }


def _step_safety() -> dict[str, Any]:
    safety = {field: False for field in contract.SAFETY_FALSE_FIELDS}
    validated = producer.validate_materialization_safety_state_v2(payload=safety)
    return {
        "ok": validated == safety,
        "fields_false": list(contract.SAFETY_FALSE_FIELDS),
        "goal_automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
    }


def _step_fixture_install() -> dict[str, Any]:
    if mat_fixture.is_fixture_installed():
        mat_fixture.uninstall_development_registration_fixture()
    before_closed = mat_fixture.production_registration_slots_closed()
    installed = mat_fixture.install_development_registration_fixture()
    after_closed = mat_fixture.production_registration_slots_closed()
    return {
        "before_production_slots_closed": before_closed,
        "install": installed,
        "after_production_slots_closed": after_closed,
        "broker_non_null": authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is not None,
        "activation_locator_non_null": (
            authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is not None
        ),
        "tcb_non_null": authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is not None,
    }


def _step_formal_red_still_blocked() -> dict[str, Any]:
    # Fixture unlocks TCB presence checks; formal implementation stays RED.
    produce = _expect_raises(
        "produce_registered_once",
        producer.produce_registered_factor_v3_formal_materialization_v2_once,
    )
    run_once = _expect_raises(
        "run_registered_once",
        runner.run_registered_factor_v3_formal_materialization_v2_once,
    )
    verify_once = _expect_raises(
        "verify_registered_once",
        runner.verify_registered_factor_v3_formal_materialization_v2_once,
    )
    # Without fixture, TCB gate raises Unavailable before RED.
    # With fixture, runner should reach RED ("registered one-shot ...").
    return {
        "produce": produce,
        "run": run_once,
        "verify": verify_once,
        "all_raised": all(
            item["raised"] for item in (produce, run_once, verify_once)
        ),
        "run_reached_red": (
            run_once["raised"]
            and "registered one-shot run" in (run_once["message"] or "")
        ),
        "verify_reached_red": (
            verify_once["raised"]
            and "registered one-shot verify" in (verify_once["message"] or "")
        ),
    }


def _step_disposable_pipeline(work_root: Path) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    fixture = create_disposable_materialization_fixture(work_root)
    kwargs = fixture.producer_kwargs()
    publication = producer._publish_disposable_materialization_contract_v2(**kwargs)
    replay = core.replay_disposable_materialization_v2(
        sqlite_artifact_path=str(fixture.sqlite_artifact_path),
        expected_sqlite_artifact_file_sha256=fixture.sqlite_artifact_file_sha256,
        producer_receipt_path=str(fixture.producer_receipt_path),
        expected_producer_receipt_file_sha256=fixture.producer_receipt_file_sha256,
    )
    claim_root = work_root / "attempt" / "claim"
    status_root = work_root / "attempt" / "status"
    artifact_root = work_root / "attempt" / "artifact"
    independent_root = work_root / "attempt" / "independent-verifier"
    publication_root = work_root / "attempt" / "publication"
    for path in (
        claim_root,
        status_root,
        artifact_root,
        independent_root,
        publication_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    claim = runner._claim_disposable_materialization_attempt_v2_once(
        semantic_input_root_sha256=_sha(
            {
                "schema": DRY_RUN_SCHEMA,
                "sqlite": fixture.sqlite_artifact_file_sha256,
            }
        ),
        claim_root=str(claim_root),
        status_root=str(status_root),
        artifact_root=str(artifact_root),
        independent_verifier_root=str(independent_root),
        publication_root=str(publication_root),
    )
    # Second claim must fail closed (create-only / no retry).
    second = _expect_raises(
        "second_claim_rejected",
        runner._claim_disposable_materialization_attempt_v2_once,
        semantic_input_root_sha256=_sha("retry-forbidden"),
        claim_root=str(claim_root),
        status_root=str(status_root),
        artifact_root=str(artifact_root),
        independent_verifier_root=str(independent_root),
        publication_root=str(publication_root),
    )
    return {
        "sqlite_artifact_path": str(fixture.sqlite_artifact_path),
        "sqlite_artifact_file_sha256": fixture.sqlite_artifact_file_sha256,
        "producer_receipt_file_sha256": fixture.producer_receipt_file_sha256,
        "independent_receipt_file_sha256": fixture.independent_receipt_file_sha256,
        "identity_root_sha256": fixture.identity_root_sha256,
        "publication": publication,
        "replay": {
            "authority_scope": replay.get("authority_scope"),
            "formal_verified": replay.get("formal_verified"),
            "verified": replay.get("verified"),
            "formal_materialization_eligible": replay.get(
                "formal_materialization_eligible"
            ),
            "structural_keys": sorted(
                k for k in replay.keys() if k not in contract.SAFETY_FALSE_FIELDS
            )[:40],
        },
        "claim": {
            "state": claim.get("state"),
            "claim_status": claim.get("claim_status"),
            "claim_create_only": claim.get("claim_create_only"),
            "claim_path": claim.get("claim_path"),
        },
        "second_claim_rejected": second,
        "ok": (
            publication.get("authority_scope") == contract.DISPOSABLE_AUTHORITY_SCOPE
            and publication.get("published") is False
            and replay.get("formal_materialization_eligible") is False
            and replay.get("verified") is False
            and claim.get("claim_status") == "CLAIMED"
            and second.get("raised") is True
        ),
    }


def _step_fixture_uninstall() -> dict[str, Any]:
    if not mat_fixture.is_fixture_installed():
        return {
            "installed": False,
            "production_slots_closed": mat_fixture.production_registration_slots_closed(),
        }
    result = mat_fixture.uninstall_development_registration_fixture()
    return {
        **result,
        "broker_is_none": authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None,
    }


def run_dry_run(*, work_root: Path) -> dict[str, Any]:
    role = _require_local_research()
    work_root.mkdir(parents=True, exist_ok=True)
    steps: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    try:
        steps["safety"] = _step_safety()
        steps["fixture_install"] = _step_fixture_install()
        steps["formal_red_still_blocked"] = _step_formal_red_still_blocked()
        steps["disposable_pipeline"] = _step_disposable_pipeline(work_root / "disposable")
    except Exception as exc:
        error = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=20),
        }
    finally:
        steps["fixture_uninstall"] = _step_fixture_uninstall()

    unsigned = {
        "schema": DRY_RUN_SCHEMA,
        "development_only": True,
        "vps_runtime_role": role,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "work_root": str(work_root.resolve()),
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "production_profile_registered": False,
        "steps": steps,
        "error": error,
        "safety": {field: False for field in contract.SAFETY_FALSE_FIELDS},
    }
    ok = (
        error is None
        and steps.get("safety", {}).get("ok") is True
        and steps.get("fixture_install", {}).get("install", {}).get("installed") is True
        and steps.get("formal_red_still_blocked", {}).get("all_raised") is True
        and steps.get("formal_red_still_blocked", {}).get("run_reached_red") is True
        and steps.get("disposable_pipeline", {}).get("ok") is True
        and steps.get("fixture_uninstall", {}).get("production_slots_closed") is True
    )
    unsigned["ok"] = ok
    evidence = {**unsigned, "evidence_sha256": _sha(unsigned)}
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Factor V3 materializer v2 development-only dry-run"
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=ROOT
        / "data"
        / "research_runs"
        / "factor_v3_materializer_v2_development_dry_run",
        help="Working directory for disposable artifacts",
    )
    parser.add_argument(
        "--evidence-out",
        type=Path,
        default=None,
        help="Optional evidence JSON path (default under work-root)",
    )
    args = parser.parse_args(argv)

    evidence = run_dry_run(work_root=args.work_root)
    evidence_path = args.evidence_out or (
        args.work_root / "evidence" / f"{evidence['evidence_sha256'][:16]}.json"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(evidence) + b"\n"
    evidence_path.write_bytes(raw)
    file_sha = hashlib.sha256(raw).hexdigest()
    pointer = {
        "schema": "factor-v3-materializer-v2-development-dry-run-pointer/v1",
        "ok": evidence["ok"],
        "evidence_path": str(evidence_path.resolve()),
        "evidence_sha256": evidence["evidence_sha256"],
        "evidence_file_sha256": file_sha,
        "development_only": True,
        "production_profile_registered": False,
        "automatic_trading_allowed": False,
    }
    pointer_path = args.work_root / "LATEST.json"
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print(f"\nevidence: {evidence_path}")
    print(f"ok: {evidence['ok']}")
    return 0 if evidence["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
