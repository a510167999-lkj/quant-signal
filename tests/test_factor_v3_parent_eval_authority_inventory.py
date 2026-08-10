from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import factor_v3_parent_eval_authority_inventory as inventory


def _write_minimal_upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    daily = repo / inventory.DEFAULT_DAILY_RUN
    daily.mkdir(parents=True)
    authority = daily / "exact-set-authority"
    receipt_rel = (
        "factor_v3_daily_basic_733_receipts/sha256/aa/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
    )
    receipt_path = authority / receipt_rel
    receipt_path.parent.mkdir(parents=True)
    receipt_body = {
        "schema": "factor-v3-daily-basic-733-exact-set-receipt/v2",
        "verified": True,
        "trade_date_count": 733,
    }
    receipt_raw = json.dumps(receipt_body, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    receipt_path.write_bytes(receipt_raw)
    import hashlib

    receipt_sha = hashlib.sha256(receipt_raw).hexdigest()
    # rewrite under cas name
    cas_rel = (
        f"factor_v3_daily_basic_733_receipts/sha256/{receipt_sha[:2]}/"
        f"{receipt_sha}.json"
    )
    cas_path = authority / cas_rel
    cas_path.parent.mkdir(parents=True, exist_ok=True)
    cas_path.write_bytes(receipt_raw)
    (daily / "state.json").write_text(
        json.dumps(
            {
                "schema": "factor-v3-daily-basic-run-state/v2",
                "status": "verified",
                "completed_session_count": 733,
                "exact_set_publication": {
                    "receipt_relative_path": cas_rel.replace("\\", "/"),
                    "receipt_sha256": receipt_sha,
                    "publication_relative_path": cas_rel.replace("\\", "/"),
                    "publication_sha256": receipt_sha,
                    "attestation_relative_path": cas_rel.replace("\\", "/"),
                    "attestation_sha256": receipt_sha,
                    "schema": "factor-v3-daily-basic-733-exact-set-publication/v2",
                },
                "receipt": {
                    "verified": True,
                    "receipt_sha256": receipt_sha,
                },
            }
        ),
        encoding="utf-8",
    )

    feature = repo / inventory.DEFAULT_FEATURE_RUN
    feature.mkdir(parents=True)
    pub_dir = (
        feature
        / "collection-publication"
        / "feature_history_collection_publication_receipts"
        / "sha256"
        / "bb"
    )
    pub_dir.mkdir(parents=True)
    issuance = {
        "schema": "audited-pit-factor-v3-feature-history-collection-publication/v1",
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
    }
    issuance_raw = json.dumps(issuance, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    issuance_sha = hashlib.sha256(issuance_raw).hexdigest()
    issuance_path = pub_dir / f"{issuance_sha}.json"
    issuance_path.write_bytes(issuance_raw)
    manifest_rel = (
        "feature_history_collection_manifest_candidates/sha256/cc/"
        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.json"
    )
    manifest_path = feature / "collection-publication" / manifest_rel
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_raw = b'{"schema":"audited-pit-factor-v3-feature-history-collection-manifest/v2"}'
    manifest_path.write_bytes(manifest_raw)
    import hashlib as _h

    manifest_sha = _h.sha256(manifest_raw).hexdigest()
    (feature / "state.json").write_text(
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
                    "session_count": 250,
                    "receipt_sha256": "d" * 64,
                },
                "collection_publication": {
                    "authority_manifest_relative_path": manifest_rel.replace("\\", "/"),
                    "authority_manifest_sha256": manifest_sha,
                    "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
                },
            }
        ),
        encoding="utf-8",
    )

    att_root = repo / inventory.DEFAULT_ATTESTATION_ROOT
    att_dir = (
        att_root
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / "ee"
    )
    att_dir.mkdir(parents=True)
    att_body = {
        "schema": "factor-v3-feature-history-frozen-source-attestation/v1",
        "verified": True,
    }
    att_raw = json.dumps(att_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    att_sha = hashlib.sha256(att_raw).hexdigest()
    (att_dir / f"{att_sha}.json").write_bytes(att_raw)
    return repo


def test_inventory_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(ValueError, match="local_research"):
        inventory.build_parent_eval_authority_inventory(repo_root=tmp_path)


def test_inventory_ok_documents_formal_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = _write_minimal_upstream(tmp_path)
    report = inventory.build_parent_eval_authority_inventory(repo_root=repo)
    assert report["ok"] is True
    assert report["stage_goal_id"] == inventory.STAGE_GOAL_ID
    assert report["development_only"] is True
    assert report["formal_materialization_eligible"] is False
    assert report["formal_parent_eval_ready"] is False
    assert report["upstream_ready_for_cross_bind"] is True
    codes = set(report["gap_codes"])
    assert "parent_source_formal_authority_receipt_missing" in codes
    assert "evaluation_formal_authority_receipt_missing" in codes
    pointer = inventory.write_inventory_evidence(
        report, output_root=tmp_path / "out"
    )
    assert pointer["ok"] is True
    assert Path(pointer["evidence_path"]).is_file()
