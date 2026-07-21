import json
from pathlib import Path

import pytest

from app.current_pool_offline_fixture_v3 import (
    FIXTURE_SCHEMA_V3,
    OfflineFixtureV3Error,
    compile_current_pool_offline_fixture_v3,
    preflight_current_pool_offline_fixture_v3,
    replay_current_pool_offline_fixture_v3_calls,
)


ROOT = Path(r"E:\AI workspace\quant-signal-lkj")
CLEAN = ROOT / "data" / "research_fixtures" / "clean_source_snapshots" / (
    "f5b2f91c2a500935f046f40023f0b9301bce4871f5d7528cebe93172b6ebbab7"
)
INVALID_RUN = ROOT / "data" / "research_artifacts" / "current_pool_risk_evidence_runs" / (
    "daaf52069b5544888214ba2f8fd6690e896b043301c49e227030a389da5d4e73"
)
INVALID_AUDIT = ROOT / "data" / "research_artifacts" / "current_pool_risk_evidence_audits" / (
    "160363910a9f36bb5f278f922329a12e25d8a7110460ebfb2bee9830f8873311.json"
)
TAIL_RUN = ROOT / "data" / "research_artifacts" / "provider_pit_tail_runs" / (
    "1a156529584194b70c9828920a5143392d7c75047896cd6e522c327950e53d75"
)


def test_v3_compiles_base_53_and_tail_6_into_new_versioned_artifact(tmp_path):
    result = compile_current_pool_offline_fixture_v3(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        tail_run_root=TAIL_RUN,
        output_parent=tmp_path / "fixture",
    )
    assert result["schema"] == FIXTURE_SCHEMA_V3
    assert result["base_calls"] == 53
    assert result["tail_calls"] == 6
    assert result["physical_raw_files"] == 56
    assert result["eligible_pool_count"] == 0
    assert result["production_recommendation_eligible"] is False
    assert result["fixture_root"]


def test_v3_preflight_and_replay_are_offline_and_bind_tail(tmp_path):
    result = compile_current_pool_offline_fixture_v3(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        tail_run_root=TAIL_RUN,
        output_parent=tmp_path / "fixture",
    )
    verified = preflight_current_pool_offline_fixture_v3(
        fixture_root=result["fixture_root"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        evidence_use="contaminated_diagnostic_historical",
    )
    assert verified["base_calls"] == 53
    assert verified["tail_calls"] == 6
    replayed = replay_current_pool_offline_fixture_v3_calls(
        fixture_root=result["fixture_root"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        evidence_use="contaminated_diagnostic_historical",
    )
    assert len(replayed["base_calls"]) == 53
    assert replayed["tail_calls"] == 6
    assert replayed["tail_evaluation_trade_date"] == "2026-07-10"


def test_v3_rejects_wrong_use_or_expected_manifest(tmp_path):
    result = compile_current_pool_offline_fixture_v3(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        tail_run_root=TAIL_RUN,
        output_parent=tmp_path / "fixture",
    )
    with pytest.raises(OfflineFixtureV3Error):
        preflight_current_pool_offline_fixture_v3(
            fixture_root=result["fixture_root"],
            expected_manifest_sha256="0" * 64,
            evidence_use="contaminated_diagnostic_historical",
        )
    with pytest.raises(OfflineFixtureV3Error):
        preflight_current_pool_offline_fixture_v3(
            fixture_root=result["fixture_root"],
            expected_manifest_sha256=result["manifest_file_sha256"],
            evidence_use="development",
        )


def test_v3_rejects_tail_manifest_tampering_after_resign(tmp_path):
    result = compile_current_pool_offline_fixture_v3(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        tail_run_root=TAIL_RUN,
        output_parent=tmp_path / "fixture",
    )
    tail_manifest = result["fixture_path"] / "tail" / "manifest.json"
    payload = json.loads(tail_manifest.read_text(encoding="utf-8"))
    payload["actual_calls"] = 5
    tail_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OfflineFixtureV3Error):
        preflight_current_pool_offline_fixture_v3(
            fixture_root=result["fixture_root"],
            expected_manifest_sha256=result["manifest_file_sha256"],
            evidence_use="contaminated_diagnostic_historical",
        )
