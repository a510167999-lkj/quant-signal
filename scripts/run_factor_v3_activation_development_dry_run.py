#!/usr/bin/env python3
"""Development-only formal-development-input-activation disposable dry-run.

Stage goal: factor-v3-activation-disposable-proof/v1

Exercises:
  1) disposable activation publish + independent differential verify
  2) public formal publish entrypoint rejects disposable scope (fail-closed)
  3) activation descriptor remains formal_materialization_eligible=False

Requires VPS_RUNTIME_ROLE=local_research. Never trades, never opens OOS.
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

import pytest  # noqa: E402

from app import factor_v3_formal_development_input_activation as activation  # noqa: E402
from app import research_goal_contract as goal  # noqa: E402
from tests import test_factor_v3_formal_development_input_activation as act_tests  # noqa: E402

DRY_RUN_SCHEMA = "factor-v3-activation-development-dry-run-evidence/v1"
STAGE_GOAL_ID = "factor-v3-activation-disposable-proof/v1"
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


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_ROLE:
        raise SystemExit(
            f"refuse dry-run: set VPS_RUNTIME_ROLE={REQUIRED_ROLE} "
            f"(got {role!r})"
        )
    return role.strip()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _install_disposable_authority_verifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same disposable verifier stubs used by activation unit tests."""

    def points_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": activation.DISPOSABLE_TEST_AUTHORITY_SCOPE,
            "contract_binding_validated": True,
            "schema": activation.POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def parent_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": activation.DISPOSABLE_TEST_AUTHORITY_SCOPE,
            "contract_binding_validated": True,
            "schema": activation.PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def evaluation_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": activation.DISPOSABLE_TEST_AUTHORITY_SCOPE,
            "contract_binding_validated": True,
            "schema": activation.FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def history_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": activation.DISPOSABLE_TEST_AUTHORITY_SCOPE,
            "contract_binding_validated": True,
            "schema": activation.FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def daily_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": activation.DISPOSABLE_TEST_AUTHORITY_SCOPE,
            "contract_binding_validated": True,
            "schema": activation.DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    monkeypatch.setattr(
        activation,
        "_verify_points_contract_authority_evidence",
        points_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_native_parent_source_authority_evidence",
        parent_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_factor_v2_evaluation_authority_evidence",
        evaluation_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_feature_history_authority_evidence",
        history_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_daily_basic_authority_evidence",
        daily_verifier,
    )


def _step_disposable_publish_and_verify(work_root: Path) -> dict[str, Any]:
    fixture = act_tests._fixture(work_root / "activation-fixture")
    publication = (
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    )
    output_root = Path(fixture["kwargs"]["output_root"])
    publication_path = act_tests._publication_path(output_root, publication)
    publication_file_sha = hashlib.sha256(publication_path.read_bytes()).hexdigest()
    manifest = _read_json(publication_path)
    descriptor_path = output_root.joinpath(
        *manifest["descriptor_relative_path"].split("/")
    )
    descriptor = _read_json(descriptor_path)

    verifier_kwargs = {
        key: value for key, value in fixture["kwargs"].items() if key != "output_root"
    }
    independent = (
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verifier_kwargs,
            activation_publication_path=publication_path,
            expected_activation_publication_sha256=publication["publication_sha256"],
            verifier_output_root=(work_root / "differential-replay").resolve(),
        )
    )
    safety_all_false = all(
        descriptor.get(field) is False for field in activation.SAFETY_FALSE_FIELDS
    )
    return {
        "publication_schema": publication.get("schema"),
        "publication_sha256": publication.get("publication_sha256"),
        "publication_path": str(publication_path),
        "publication_file_sha256": publication_file_sha,
        "activation_root_sha256": manifest.get("activation_root_sha256"),
        "descriptor_path": str(descriptor_path),
        "authority_scope": descriptor.get("authority_scope"),
        "authority_status": descriptor.get("authority_status"),
        "test_fixture_only": descriptor.get("test_fixture_only"),
        "activation_verified": descriptor.get("activation_verified"),
        "verified": descriptor.get("verified"),
        "source_authority_complete": descriptor.get("source_authority_complete"),
        "formal_materialization_eligible": descriptor.get(
            "formal_materialization_eligible"
        ),
        "formal_materialization_performed": descriptor.get(
            "formal_materialization_performed"
        ),
        "safety_all_false": safety_all_false,
        "independent": {
            "differential_contract_replay_performed": independent.get(
                "differential_contract_replay_performed"
            ),
            "independent_public_replay_performed": independent.get(
                "independent_public_replay_performed"
            ),
            "verified": independent.get("verified"),
            "receipt_relative_path": independent.get("receipt_relative_path"),
        },
        "ok": (
            publication.get("schema") == activation.PUBLICATION_SCHEMA
            and publication_file_sha == publication.get("publication_sha256")
            and descriptor.get("authority_scope")
            == activation.DISPOSABLE_TEST_AUTHORITY_SCOPE
            and descriptor.get("test_fixture_only") is True
            and descriptor.get("activation_verified") is False
            and descriptor.get("verified") is False
            and descriptor.get("formal_materialization_eligible") is False
            and descriptor.get("formal_materialization_performed") is False
            and safety_all_false
            and independent.get("differential_contract_replay_performed") is True
            and independent.get("independent_public_replay_performed") is False
            and independent.get("verified") is False
        ),
    }


def _step_formal_entrypoint_rejects_disposable(work_root: Path) -> dict[str, Any]:
    fixture = act_tests._fixture(work_root / "formal-reject-fixture")
    before = {
        path: path.stat().st_mtime_ns if path.exists() else None
        for path in (work_root / "formal-reject-fixture").rglob("*")
        if path.is_file()
    }
    raised = False
    exception_type = None
    message = None
    try:
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    except activation.FactorV3FormalDevelopmentInputActivationError as exc:
        raised = True
        exception_type = type(exc).__name__
        message = str(exc)[:500]
    except Exception as exc:  # unexpected
        raised = True
        exception_type = type(exc).__name__
        message = f"unexpected: {exc}"[:500]
    after_files = [
        path
        for path in (work_root / "formal-reject-fixture").rglob("*")
        if path.is_file()
    ]
    new_or_changed = 0
    for path in after_files:
        previous = before.get(path)
        if previous is None or path.stat().st_mtime_ns != previous:
            # fixture itself creates inputs; only activation-output should stay empty
            if "activation-output" in path.parts:
                new_or_changed += 1
    activation_output = Path(fixture["kwargs"]["output_root"])
    activation_output_empty = (
        not activation_output.exists()
        or not any(activation_output.rglob("*"))
    )
    return {
        "raised": raised,
        "exception_type": exception_type,
        "message": message,
        "activation_output_empty": activation_output_empty,
        "activation_output_new_files": new_or_changed,
        "ok": raised and activation_output_empty,
    }


def _inventory_local_authority_artifacts(repo_root: Path) -> dict[str, Any]:
    daily_state = (
        repo_root
        / "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_development_733_http_publish/state.json"
    )
    feature_state = (
        repo_root
        / "data/research_runs/audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250/state.json"
    )
    attestation_root = (
        repo_root
        / "data/research_artifacts/factor_v3_feature_history_frozen_source_attestation_v6_http"
    )
    materializer_pointer = (
        repo_root
        / "data/research_runs/factor_v3_materializer_v2_development_dry_run/LATEST.json"
    )

    def state_summary(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"present": False, "path": str(path)}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"present": True, "path": str(path), "parseable": False}
        receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
        return {
            "present": True,
            "path": str(path),
            "parseable": True,
            "status": payload.get("status"),
            "completed_session_count": payload.get("completed_session_count"),
            "receipt_verified": receipt.get("verified"),
            "authority_status": receipt.get("authority_status"),
            "factor_materialization_eligible": receipt.get(
                "factor_materialization_eligible"
            ),
        }

    attestation_files = []
    if attestation_root.is_dir():
        attestation_files = sorted(
            str(p.relative_to(repo_root))
            for p in attestation_root.rglob("*.json")
            if p.is_file()
        )[:20]
    materializer_ok = False
    if materializer_pointer.is_file():
        try:
            materializer_ok = (
                json.loads(materializer_pointer.read_text(encoding="utf-8")).get("ok")
                is True
            )
        except (OSError, json.JSONDecodeError):
            materializer_ok = False
    return {
        "daily_basic": state_summary(daily_state),
        "feature_history": state_summary(feature_state),
        "feature_history_attestation_v6_http": {
            "root": str(attestation_root),
            "present": attestation_root.is_dir(),
            "json_files_sample": attestation_files,
            "json_file_count": len(attestation_files),
        },
        "materializer_development_dry_run_ok": materializer_ok,
    }


def run_dry_run(*, work_root: Path, repo_root: Path) -> dict[str, Any]:
    role = _require_local_research()
    work_root.mkdir(parents=True, exist_ok=True)
    steps: dict[str, Any] = {
        "local_authority_inventory": _inventory_local_authority_artifacts(repo_root)
    }
    error: dict[str, Any] | None = None
    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_disposable_authority_verifiers(monkeypatch)
        steps["disposable_publish_and_verify"] = _step_disposable_publish_and_verify(
            work_root / "disposable"
        )
        steps["formal_entrypoint_rejects_disposable"] = (
            _step_formal_entrypoint_rejects_disposable(work_root / "formal-reject")
        )
    except Exception as exc:
        error = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=25),
        }
    finally:
        monkeypatch.undo()

    unsigned = {
        "schema": DRY_RUN_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "development_only": True,
        "vps_runtime_role": role,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "work_root": str(work_root.resolve()),
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "production_profile_registered": False,
        "steps": steps,
        "error": error,
        "safety": {field: False for field in activation.SAFETY_FALSE_FIELDS},
    }
    ok = (
        error is None
        and steps.get("disposable_publish_and_verify", {}).get("ok") is True
        and steps.get("formal_entrypoint_rejects_disposable", {}).get("ok") is True
    )
    unsigned["ok"] = ok
    return {**unsigned, "evidence_sha256": _sha(unsigned)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Factor V3 activation disposable development dry-run"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=ROOT
        / "data"
        / "research_runs"
        / "factor_v3_activation_development_dry_run",
    )
    parser.add_argument("--evidence-out", type=Path, default=None)
    args = parser.parse_args(argv)

    evidence = run_dry_run(work_root=args.work_root, repo_root=args.repo_root.resolve())
    evidence_path = args.evidence_out or (
        args.work_root / "evidence" / f"{evidence['evidence_sha256'][:16]}.json"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(evidence) + b"\n"
    evidence_path.write_bytes(raw)
    file_sha = hashlib.sha256(raw).hexdigest()
    pointer = {
        "schema": "factor-v3-activation-development-dry-run-pointer/v1",
        "stage_goal_id": STAGE_GOAL_ID,
        "ok": evidence["ok"],
        "evidence_path": str(evidence_path.resolve()),
        "evidence_sha256": evidence["evidence_sha256"],
        "evidence_file_sha256": file_sha,
        "development_only": True,
        "production_profile_registered": False,
        "automatic_trading_allowed": False,
        "formal_materialization_eligible": False,
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
