import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import current_pool_offline_fixture_v4 as fixture_v4


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


def _compile(tmp_path):
    return fixture_v4.compile_current_pool_offline_fixture_v4(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        tail_run_root=TAIL_RUN,
        output_parent=tmp_path / "fixture",
    )


def _resign_manifest_and_receipt(path, manifest):
    manifest["producer_binding_sha256"] = fixture_v4._sha(manifest["producer_binding"])
    manifest["content_canonical_sha256"] = fixture_v4._sha(
        {
            key: value
            for key, value in manifest.items()
            if key not in {"content_canonical_sha256", "fixture_id", "canonical_sha256"}
        }
    )
    manifest["fixture_id"] = fixture_v4._sha(fixture_v4._identity(manifest))
    manifest["canonical_sha256"] = fixture_v4._sha(
        {key: value for key, value in manifest.items() if key != "canonical_sha256"}
    )
    relocated = path.parent.parent / manifest["fixture_id"]
    shutil.copytree(path.parent, relocated, copy_function=shutil.copyfile)
    path = relocated / "manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    receipt_path = relocated / "fixture-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.update(
        {
            "fixture_id": manifest["fixture_id"],
            "manifest_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "manifest_canonical_sha256": manifest["canonical_sha256"],
            "producer_binding_sha256": manifest["producer_binding_sha256"],
        }
    )
    receipt["receipt_canonical_sha256"] = fixture_v4._sha(
        {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
    )
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    return relocated / "manifest.json"


def test_v4_compiles_fresh_successor_and_binds_current_code_and_tests(tmp_path):
    result = _compile(tmp_path)

    manifest = json.loads(result["manifest_path"].read_text(encoding="utf-8"))
    assert result["schema"] == fixture_v4.FIXTURE_SCHEMA_V4
    assert manifest["base_calls"] == 53
    assert manifest["tail_calls"] == 6
    assert manifest["physical_raw_files"] == 56
    assert manifest["producer_binding"] == fixture_v4.current_producer_binding_v4()
    assert result["eligible_pool_count"] == 0
    assert result["production_recommendation_eligible"] is False


def test_v4_preflight_and_replay_are_offline_and_bind_current_producer(tmp_path):
    result = _compile(tmp_path)

    verified = fixture_v4.preflight_current_pool_offline_fixture_v4(
        fixture_root=result["fixture_root"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        evidence_use="contaminated_diagnostic_historical",
    )
    replayed = fixture_v4.replay_current_pool_offline_fixture_v4_calls(
        fixture_root=result["fixture_root"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        evidence_use="contaminated_diagnostic_historical",
    )

    assert verified["producer_binding_sha256"] == result["producer_binding_sha256"]
    assert len(replayed["base_calls"]) == 53
    assert replayed["tail_calls"] == 6
    assert replayed["network_calls"] == 0


def test_v4_rejects_wrong_use_or_expected_manifest(tmp_path):
    result = _compile(tmp_path)

    with pytest.raises(fixture_v4.OfflineFixtureV4Error):
        fixture_v4.preflight_current_pool_offline_fixture_v4(
            fixture_root=result["fixture_root"],
            expected_manifest_sha256="0" * 64,
            evidence_use="contaminated_diagnostic_historical",
        )
    with pytest.raises(fixture_v4.OfflineFixtureV4Error):
        fixture_v4.preflight_current_pool_offline_fixture_v4(
            fixture_root=result["fixture_root"],
            expected_manifest_sha256=result["manifest_file_sha256"],
            evidence_use="development",
        )


def test_v4_rejects_full_resign_of_producer_binding_tamper(tmp_path):
    result = _compile(tmp_path)
    manifest_path = result["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["producer_binding"]["entries"][0]["sha256"] = "0" * 64
    manifest_path = _resign_manifest_and_receipt(manifest_path, manifest)

    with pytest.raises(fixture_v4.OfflineFixtureV4Error, match="producer binding"):
        fixture_v4.preflight_current_pool_offline_fixture_v4(
            fixture_root=manifest_path.parent,
            expected_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            evidence_use="contaminated_diagnostic_historical",
        )
