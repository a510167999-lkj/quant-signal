from __future__ import annotations

import json
from pathlib import Path

from app import factor_v3_training_readiness_audit as audit
from app import research_goal_contract as goal


def test_stage_goal_is_development_only_and_aligned_with_research_goal() -> None:
    summary = audit.STAGE_GOAL_SUMMARY.lower()
    assert audit.STAGE_GOAL_ID == "factor-v3-train-locked-formal-data-receipts/v1"
    assert "2026-08-01" in audit.STAGE_GOAL_SUMMARY or "train" in summary
    assert "自动交易" in audit.STAGE_GOAL_SUMMARY or "trading" in summary
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 50.0
    assert goal.TARGET_MAX_DRAWDOWN_PCT == 15.0
    assert "BSE" in goal.DOWNSTREAM_EXCLUDED_SEGMENTS
    assert "SSE_STAR" in goal.DOWNSTREAM_EXCLUDED_SEGMENTS


def test_audit_reports_blocking_gaps_without_raising(tmp_path: Path, monkeypatch) -> None:
    # Isolate from real workspace env/files for deterministic unit behavior.
    monkeypatch.chdir(tmp_path)
    report = audit.run_factor_v3_training_readiness_audit(repo_root=tmp_path)
    assert report.stage_goal_id == audit.STAGE_GOAL_ID
    assert report.ready_for_formal_development_materialization is False
    assert "jiaoch_credential_available" in report.blocking_gaps
    assert "daily_basic_state_present" in report.blocking_gaps
    assert "activation_development_dry_run_ok" in report.blocking_gaps
    assert "materializer_development_dry_run_ok" in report.blocking_gaps
    assert "parent_eval_authority_inventory_ok" in report.blocking_gaps
    assert "train_locked_formal_data_receipts_ok" in report.blocking_gaps
    markdown = audit.render_markdown(report)
    assert "Blocking gaps" in markdown
    assert "50" in markdown


def test_dry_run_pointer_audit_accepts_valid_activation_evidence(tmp_path: Path) -> None:
    pointer = tmp_path / "LATEST.json"
    pointer.write_text(
        json.dumps(
            {
                "ok": True,
                "development_only": True,
                "stage_goal_id": audit.ACTIVATION_STAGE_GOAL_ID,
                "evidence_sha256": "a" * 64,
                "production_profile_registered": False,
                "automatic_trading_allowed": False,
                "formal_materialization_eligible": False,
            }
        ),
        encoding="utf-8",
    )
    check = audit._audit_dry_run_pointer(
        pointer,
        check_id="activation_development_dry_run_ok",
        require_stage_goal_id=audit.ACTIVATION_STAGE_GOAL_ID,
    )
    assert check.ok is True


def test_inventory_pointer_audit_accepts_stage_goal(tmp_path: Path) -> None:
    pointer = tmp_path / "LATEST.json"
    pointer.write_text(
        json.dumps(
            {
                "ok": True,
                "development_only": True,
                "stage_goal_id": audit.PARENT_EVAL_INVENTORY_STAGE_GOAL_ID,
                "inventory_sha256": "b" * 64,
                "production_profile_registered": False,
                "automatic_trading_allowed": False,
                "formal_materialization_eligible": False,
                "formal_parent_eval_ready": False,
            }
        ),
        encoding="utf-8",
    )
    check = audit._audit_dry_run_pointer(
        pointer,
        check_id="parent_eval_authority_inventory_ok",
        require_stage_goal_id=audit.PARENT_EVAL_INVENTORY_STAGE_GOAL_ID,
    )
    assert check.ok is True


def test_train_locked_pointer_audit_accepts_stage_goal(tmp_path: Path) -> None:
    pointer = tmp_path / "LATEST.json"
    pointer.write_text(
        json.dumps(
            {
                "ok": True,
                "development_only": True,
                "stage_goal_id": audit.STAGE_GOAL_ID,
                "bundle_sha256": "c" * 64,
                "production_profile_registered": False,
                "automatic_trading_allowed": False,
                "formal_materialization_eligible": False,
                "daily_incremental_sync_required": False,
                "upstream_formal_data_complete": True,
                "parent_eval_formal_complete": False,
            }
        ),
        encoding="utf-8",
    )
    check = audit._audit_dry_run_pointer(
        pointer,
        check_id="train_locked_formal_data_receipts_ok",
        require_stage_goal_id=audit.STAGE_GOAL_ID,
    )
    assert check.ok is True


def test_daily_basic_success_path_detected(tmp_path: Path) -> None:
    run = tmp_path / "daily"
    run.mkdir()
    (run / "state.json").write_text(
        json.dumps(
            {
                "schema": "factor-v3-daily-basic-run-state/v2",
                "status": "succeeded",
                "completed_session_count": 733,
            }
        ),
        encoding="utf-8",
    )
    checks = {c.id: c for c in audit.audit_daily_basic_run(run)}
    assert checks["daily_basic_run_succeeded"].ok is True
    assert checks["daily_basic_session_coverage_733"].ok is True


def test_feature_history_history_only_contract_ok(tmp_path: Path) -> None:
    run = tmp_path / "feature"
    run.mkdir()
    (run / "collection-publication").mkdir()
    (run / "state.json").write_text(
        json.dumps(
            {
                "schema": "factor-v3-feature-history-run-state/v2",
                "status": "failed",
                "completed_session_count": 250,
                "receipt": {
                    "verified": True,
                    "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
                    "feature_history_only": True,
                    "factor_materialization_eligible": False,
                    "experiment_launch_eligible": False,
                    "embargo_consumed": False,
                    "final_oos_consumed": False,
                    "production_profile_registered": False,
                    "production_recommendation_eligible": False,
                    "session_count": 250,
                },
                "collection_publication": {
                    "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
                },
            }
        ),
        encoding="utf-8",
    )
    checks = {c.id: c for c in audit.audit_feature_history_run(run)}
    assert checks["feature_history_prewindow_250"].ok is True
    assert checks["feature_history_receipt_verified"].ok is True
    assert checks["feature_history_history_only_contract_ok"].ok is True
    assert checks["feature_history_no_partial_publication_dirs"].ok is True
    assert "feature_history_formal_materialization_eligible" not in checks


def test_code_registration_gates_include_development_fixture() -> None:
    checks = {c.id: c for c in audit.audit_code_registration_gates()}
    assert checks["materializer_development_registration_fixture_ready"].ok is True
    assert checks["materializer_development_registration_fixture_ready"].blocking is True
    # Durable formal TCB remains intentionally unregistered (non-blocking).
    assert checks["materializer_formal_registration_ready"].blocking is False
    assert checks["materializer_production_registration_closed"].ok is True
