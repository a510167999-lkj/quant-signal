from __future__ import annotations

import json
from pathlib import Path

from app import factor_v3_training_readiness_audit as audit
from app import research_goal_contract as goal


def test_stage_goal_is_development_only_and_aligned_with_research_goal() -> None:
    summary = audit.STAGE_GOAL_SUMMARY.lower()
    assert "development" in summary
    assert "embargo" in summary or "oos" in summary or "生产" in audit.STAGE_GOAL_SUMMARY
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
    markdown = audit.render_markdown(report)
    assert "Blocking gaps" in markdown
    assert "50" in markdown


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
