from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.research_trusted_roots import (
    TrustedFixtureRootV1,
    TrustedRootError,
    TrustedSnapshotRootV1,
    verify_fixture_v2,
    verify_snapshot_v2,
)
from tests.test_current_pool_offline_fixture_v2 import (
    _canonical_bytes,
    _resign_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "research_fixtures" / "clean_source_snapshots" / (
    "f5b2f91c2a500935f046f40023f0b9301bce4871f5d7528cebe93172b6ebbab7"
)
FIXTURE = ROOT / "data" / "research_fixtures" / "current_pool_offline_v2" / (
    "4df9bf98fa90be11c309d0d1e9e557ffb24f00c0ca65d9f2f8aa039e1cf8ff0c"
)


def _sha(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _snapshot_expected() -> TrustedSnapshotRootV1:
    values = {
        "schema": "trusted-snapshot-root/v1",
        "artifact_schema": "clean-source-snapshot/v2",
        "directory_id": "f5b2f91c2a500935f046f40023f0b9301bce4871f5d7528cebe93172b6ebbab7",
        "created_at_utc": "2026-07-16T05:59:35.503000+00:00",
        "manifest_file_sha256": "2180110c2283245f104590ab9d9e5c3e390bc318aa92ff358ba241643f6fb21b",
        "manifest_canonical_sha256": "cb1c421477c8476e29942202683575d52f1d183f1eda9e13f5a216f1b76114ad",
        "manifest_content_canonical_sha256": "e5088e98653f271417a2f211938f5d5e08bf3e4044da5d276867ead4c125271a",
        "receipt_file_sha256": "5a516b3f736d90a90b35ec49b565257173656e00f2ee312d86e0478383843cbc",
        "receipt_canonical_sha256": "5958c675d33f4813b6d1bebc301e31e4d2306cfa3b888bcf74b2ad50a998bd71",
        "receipt_identity_canonical_sha256": "2f93c8636b1b065d547f724e36fa8c3f435c98e5ef72a56e16d106fafd11c7a5",
        "audit_file_sha256": "e47f6a3aedf2f65633d9d9c2f7d0c6a0ff4beb7d77b615585e9b2d53403403b8",
        "audit_canonical_sha256": "c42b6c4d230698a60d5d2bf55b6ed68fb8543d7e40647610653ed45b7dadf17c",
        "source_files_root_sha256": "70a919db954d06e6b292f8ddbffe0a044f10793e6fdd2bce8960b72b7f71075d",
        "payload_file_count": 61,
        "payload_tree_sha256": "875aa6e2c909a44107766a5a1dcdbb798bc6b6877cb9c929f679cf04925f4552",
        "payload_directory_set_sha256": "f2fe911cd7a62a95ee0cb73b814ec8c4a1dbdd795f3f06430ab9b5b5c6ceed3d",
        "origin_source_tree_sha256": "089d14d1bef5daf597753253f3aba75ea16a9bd3a5587fc9fc2b421b919f7d0a",
        "origin_content_manifest_sha256": "518e39a5e8a0f0e7bd63be5145200eced5337a1baad25a322a2c0d851784dcd5",
        "origin_import_receipt_canonical_sha256": "f5ef6c48022945b9e57474b2115d9438ff0371832026f198266dcb27414894f1",
    }
    return TrustedSnapshotRootV1(**values, root_canonical_sha256=_sha(values))


def _fixture_expected() -> TrustedFixtureRootV1:
    snapshot = _snapshot_expected()
    values = {
        "schema": "trusted-fixture-root/v1",
        "artifact_schema": "current-pool-offline-fixture/v2",
        "fixture_id": "4df9bf98fa90be11c309d0d1e9e557ffb24f00c0ca65d9f2f8aa039e1cf8ff0c",
        "as_of": "2026-07-03",
        "evidence_use": "contaminated_diagnostic_historical",
        "manifest_file_sha256": "9031271f28f9cacca4ac21c49d056c6d47f3e5e8da4467a72af818f8d27fe73b",
        "manifest_canonical_sha256": "b135d3b148052c0d8856c4b2189fcbb07ef9dae6a106a9c877e1c466fdfd0f41",
        "manifest_content_canonical_sha256": "5eddf26fdc532d378abb3ead6a80b7e11250bdda7d6f285c7d19335cfec0ad22",
        "receipt_file_sha256": "654552af93bf0d256d4db2b177156abdd121a4bdd30060498040399d54c97054",
        "receipt_canonical_sha256": "92be6ee06ef081210776b80269bbcac1a2202c5aabc2410acfe4f9a8ba44daa1",
        "adjudication_file_sha256": "0a9a0c7d85cda26efc48342ee25b0a0e617bb7930c7f4ed38c232bcc41441587",
        "adjudication_canonical_sha256": "0d4b701f63c6e3b5083c25ed20ae35a1cd6ed74151e047cdb1e19e79de1acc89",
        "declared_files_root_sha256": "abecdf862fb8891faf2318d81b4cdcb68c3aa1d5de8b44396e85e16c34cbdfc8",
        "calls_root_sha256": "869df303c0cb2b793cd2371754f6f7127eaedaf6fe46f525e823773f9b885d69",
        "source_segment_calls_root_sha256": "5856cd3512faa860314c1965accb922c40e5e138f3d551ac3ecd6a37f5aad464",
        "risk_segment_calls_root_sha256": "c723572af158cb8754ce6ea4fa7560948805c0759e46a447cd863926b9449eb1",
        "payload_file_count": 106,
        "payload_tree_sha256": "23291463d5a5b786dc5a881303e011e435599e6d35de8e4f37e7d3b337b3e0b3",
        "payload_directory_set_sha256": "0df24456fad99139c6bea3de8fd25d8dc1a318f142638dda6e0a65ef4d9c9364",
        "call_count": 53,
        "physical_raw_file_count": 50,
        "invalid_run_id": "daaf52069b5544888214ba2f8fd6690e896b043301c49e227030a389da5d4e73",
        "invalid_run_tree_sha256": "9b2eb8aae31f04059a3d81edc282d299facf1faaf9727b282e8510c9b1515f86",
        "invalid_audit_file_sha256": "2a0fc55630b673045d1f85cd13dcbb5c7846b0147dd8701216e12da1d08bdd72",
        "invalid_audit_canonical_sha256": "160363910a9f36bb5f278f922329a12e25d8a7110460ebfb2bee9830f8873311",
        "snapshot": snapshot,
    }
    serializable = {**values, "snapshot": snapshot.to_dict()}
    return TrustedFixtureRootV1(**values, root_canonical_sha256=_sha(serializable))


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / FIXTURE.name
    shutil.copytree(FIXTURE, target)
    return target


def _reject_fixture(
    root: Path,
    expected: TrustedFixtureRootV1 | None = None,
    *,
    snapshot_root: Path = SNAPSHOT,
) -> None:
    with pytest.raises(TrustedRootError):
        verify_fixture_v2(
            root,
            snapshot_root=snapshot_root,
            expected=expected or _fixture_expected(),
            evidence_use="contaminated_diagnostic_historical",
        )


def _tree_rows(root: Path) -> list[dict]:
    rows = []
    for path in sorted((path for path in root.rglob("*") if path.is_file())):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return rows


def _directory_set_sha(root: Path) -> str:
    directories = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir()
    )
    return _sha(directories)


def _expected_from_resigned_roots(
    snapshot_root: Path,
    fixture_root: Path,
) -> tuple[TrustedSnapshotRootV1, TrustedFixtureRootV1]:
    snapshot_manifest_path = snapshot_root / "clean-snapshot-manifest.json"
    snapshot_receipt_path = snapshot_root / "clean-snapshot-receipt.json"
    snapshot_audit_path = snapshot_root / "contamination-audit.json"
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    snapshot_receipt = json.loads(snapshot_receipt_path.read_text(encoding="utf-8"))
    snapshot_audit = json.loads(snapshot_audit_path.read_text(encoding="utf-8"))
    snapshot_values = _snapshot_expected().to_dict()
    snapshot_values.update(
        {
            "directory_id": snapshot_root.name,
            "created_at_utc": snapshot_manifest["created_at_utc"],
            "manifest_file_sha256": hashlib.sha256(
                snapshot_manifest_path.read_bytes()
            ).hexdigest(),
            "manifest_canonical_sha256": snapshot_manifest[
                "manifest_canonical_sha256"
            ],
            "manifest_content_canonical_sha256": snapshot_manifest[
                "manifest_content_canonical_sha256"
            ],
            "receipt_file_sha256": hashlib.sha256(
                snapshot_receipt_path.read_bytes()
            ).hexdigest(),
            "receipt_canonical_sha256": snapshot_receipt[
                "receipt_canonical_sha256"
            ],
            "receipt_identity_canonical_sha256": snapshot_receipt[
                "receipt_identity_canonical_sha256"
            ],
            "audit_file_sha256": hashlib.sha256(
                snapshot_audit_path.read_bytes()
            ).hexdigest(),
            "audit_canonical_sha256": snapshot_audit["audit_canonical_sha256"],
            "source_files_root_sha256": snapshot_manifest[
                "source_files_root_sha256"
            ],
            "payload_file_count": len(_tree_rows(snapshot_root)),
            "payload_tree_sha256": _sha(_tree_rows(snapshot_root)),
            "payload_directory_set_sha256": _directory_set_sha(snapshot_root),
        }
    )
    snapshot_values["root_canonical_sha256"] = _sha(
        {
            key: value
            for key, value in snapshot_values.items()
            if key != "root_canonical_sha256"
        }
    )
    snapshot = TrustedSnapshotRootV1.from_dict(snapshot_values)
    fixture_manifest_path = fixture_root / "manifest.json"
    fixture_receipt_path = fixture_root / "fixture-receipt.json"
    adjudication_path = fixture_root / "risk-semantic-empty-adjudication.json"
    fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
    fixture_receipt = json.loads(fixture_receipt_path.read_text(encoding="utf-8"))
    adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
    fixture_values = _fixture_expected().to_dict()
    fixture_values.update(
        {
            "fixture_id": fixture_root.name,
            "manifest_file_sha256": hashlib.sha256(
                fixture_manifest_path.read_bytes()
            ).hexdigest(),
            "manifest_canonical_sha256": fixture_manifest["canonical_sha256"],
            "manifest_content_canonical_sha256": fixture_manifest[
                "content_canonical_sha256"
            ],
            "receipt_file_sha256": hashlib.sha256(
                fixture_receipt_path.read_bytes()
            ).hexdigest(),
            "receipt_canonical_sha256": fixture_receipt[
                "receipt_canonical_sha256"
            ],
            "adjudication_file_sha256": hashlib.sha256(
                adjudication_path.read_bytes()
            ).hexdigest(),
            "adjudication_canonical_sha256": adjudication[
                "adjudication_canonical_sha256"
            ],
            "declared_files_root_sha256": fixture_manifest[
                "declared_files_root_sha256"
            ],
            "calls_root_sha256": fixture_manifest["calls_root_sha256"],
            "source_segment_calls_root_sha256": fixture_manifest["segments"][0][
                "calls_root_sha256"
            ],
            "risk_segment_calls_root_sha256": fixture_manifest["segments"][1][
                "calls_root_sha256"
            ],
            "payload_file_count": len(_tree_rows(fixture_root)),
            "payload_tree_sha256": _sha(_tree_rows(fixture_root)),
            "payload_directory_set_sha256": _directory_set_sha(fixture_root),
            "physical_raw_file_count": fixture_receipt["physical_raw_file_count"],
            "snapshot": snapshot.to_dict(),
        }
    )
    fixture_values["root_canonical_sha256"] = _sha(
        {
            key: value
            for key, value in fixture_values.items()
            if key != "root_canonical_sha256"
        }
    )
    return snapshot, TrustedFixtureRootV1.from_dict(fixture_values)


def _assert_snapshot_identity_resigned(root: Path) -> None:
    manifest_path = root / "clean-snapshot-manifest.json"
    receipt_path = root / "clean-snapshot-receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest_unsigned = dict(manifest)
    assert manifest_unsigned.pop("manifest_canonical_sha256") == _sha(manifest_unsigned)
    manifest_content = dict(manifest)
    for field in (
        "manifest_canonical_sha256",
        "manifest_content_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
    ):
        manifest_content.pop(field, None)
    assert manifest["manifest_content_canonical_sha256"] == _sha(manifest_content)
    receipt_unsigned = dict(receipt)
    assert receipt_unsigned.pop("receipt_canonical_sha256") == _sha(receipt_unsigned)
    receipt_identity = dict(receipt)
    for field in (
        "receipt_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
        "manifest",
    ):
        receipt_identity.pop(field, None)
    assert receipt["receipt_identity_canonical_sha256"] == _sha(receipt_identity)
    identity = {
        "schema": "clean-source-snapshot-identity/v2",
        "manifest_content_canonical_sha256": manifest[
            "manifest_content_canonical_sha256"
        ],
        "receipt_identity_canonical_sha256": receipt[
            "receipt_identity_canonical_sha256"
        ],
        "source_tree_sha256": manifest["source_lineage"]["old_logical_tree_sha256"],
        "declared_files_root_sha256": manifest["source_files_root_sha256"],
        "contaminated_physical_tree_sha256": json.loads(
            (root / "contamination-audit.json").read_text(encoding="utf-8")
        )["actual_files_root_sha256"],
    }
    assert root.name == manifest["snapshot_id"] == receipt["snapshot_id"] == _sha(identity)
    assert manifest["source_files"] == _tree_rows(root / "source")
    assert manifest["source_files_root_sha256"] == _sha(manifest["source_files"])
    assert receipt["manifest"] == {
        "path": "clean-snapshot-manifest.json",
        "bytes": manifest_path.stat().st_size,
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "canonical_sha256": manifest["manifest_canonical_sha256"],
    }


def _assert_fixture_identity_resigned(root: Path) -> None:
    manifest_path = root / "manifest.json"
    receipt_path = root / "fixture-receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest_unsigned = dict(manifest)
    assert manifest_unsigned.pop("canonical_sha256") == _sha(manifest_unsigned)
    content = dict(manifest)
    content.pop("canonical_sha256", None)
    content.pop("content_canonical_sha256", None)
    content.pop("fixture_id", None)
    assert manifest["content_canonical_sha256"] == _sha(content)
    identity = {
        "schema": "current-pool-offline-fixture-identity/v2",
        "content_canonical_sha256": manifest["content_canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "source_lineage": manifest["source_lineage"],
        "risk_adjudication": manifest["risk_adjudication"],
    }
    assert root.name == manifest["fixture_id"] == _sha(identity)
    assert manifest["calls_root_sha256"] == _sha(manifest["calls"])
    assert manifest["segments"][0]["calls_root_sha256"] == _sha(manifest["calls"][:14])
    assert manifest["segments"][1]["calls_root_sha256"] == _sha(manifest["calls"][14:])
    declared = {row["path"]: row for row in manifest["declared_files"]}
    assert manifest["declared_files_root_sha256"] == _sha(manifest["declared_files"])
    for relative, descriptor in declared.items():
        path = root / relative
        assert descriptor == {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    for call in manifest["calls"]:
        raw = root / call["raw_path"]
        evidence = root / call["evidence_path"]
        assert call["raw_bytes"] == raw.stat().st_size
        assert call["raw_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
        assert call["evidence_bytes"] == evidence.stat().st_size
        assert call["evidence_file_sha256"] == hashlib.sha256(
            evidence.read_bytes()
        ).hexdigest()
        evidence_payload = json.loads(evidence.read_text(encoding="utf-8"))
        canonical_field = (
            "evidence_canonical_sha256"
            if call["source_index"] < 14
            else "receipt_canonical_sha256"
        )
        evidence_unsigned = dict(evidence_payload)
        assert evidence_unsigned.pop(canonical_field) == _sha(evidence_unsigned)
        assert call["evidence_canonical_sha256"] == evidence_payload[canonical_field]
    receipt_unsigned = dict(receipt)
    assert receipt_unsigned.pop("receipt_canonical_sha256") == _sha(receipt_unsigned)
    assert receipt["fixture_id"] == manifest["fixture_id"]
    assert receipt["manifest_file_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert receipt["manifest_canonical_sha256"] == manifest["canonical_sha256"]
    assert receipt["declared_files_root_sha256"] == manifest[
        "declared_files_root_sha256"
    ]


def _full_resign_risk_raw(
    root: Path,
    source_index: int,
    mutate,
    *,
    semantic_empty: bool | None = None,
) -> Path:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    call = manifest["calls"][source_index]
    old_raw_relative = call["raw_path"]
    old_raw_path = root / old_raw_relative
    envelope = json.loads(old_raw_path.read_text(encoding="utf-8"))
    mutate(envelope)
    raw = _canonical_bytes(envelope) + b"\n"
    raw_sha = hashlib.sha256(raw).hexdigest()
    new_raw_relative = f"raw/source-risk/{source_index}/{raw_sha}.json"
    new_raw_path = root / new_raw_relative
    new_raw_path.parent.mkdir(parents=True, exist_ok=True)
    new_raw_path.write_bytes(raw)
    old_raw_path.unlink()
    fields = envelope["data"]["fields"]
    items = envelope["data"]["items"]
    normalized = [dict(zip(fields, row, strict=True)) for row in items]
    old_evidence_relative = call["evidence_path"]
    old_evidence_path = root / old_evidence_relative
    evidence = json.loads(old_evidence_path.read_text(encoding="utf-8"))
    evidence["raw_bytes"] = len(raw)
    evidence["raw_sha256"] = raw_sha
    evidence["rows_sha256"] = _sha(normalized)
    evidence["row_count"] = len(items)
    evidence["response_fields"] = fields
    evidence["response_headers"]["content-length"] = str(len(raw))
    evidence["semantic_empty"] = not items
    evidence["raw_path"] = (
        f"raw/{int(evidence['run_sequence']):02d}_{source_index}_"
        f"{call['endpoint']}_{raw_sha}.json"
    )
    if not items:
        evidence["partition_date_min"] = None
        evidence["partition_date_max"] = None
    elif call["endpoint"] == "namechange":
        dates = [row[2] for row in items]
        evidence["partition_date_min"] = min(dates)
        evidence["partition_date_max"] = max(dates)
    evidence_unsigned = dict(evidence)
    evidence_unsigned.pop("receipt_canonical_sha256", None)
    evidence["receipt_canonical_sha256"] = _sha(evidence_unsigned)
    new_evidence_relative = f"evidence/risk/{evidence['receipt_canonical_sha256']}.json"
    new_evidence_path = root / new_evidence_relative
    new_evidence_path.write_bytes(_canonical_bytes(evidence) + b"\n")
    old_evidence_path.unlink()
    call.update(
        {
            "raw_path": new_raw_relative,
            "raw_bytes": len(raw),
            "raw_sha256": raw_sha,
            "raw_items_sha256": _sha(items),
            "normalized_rows_sha256": _sha(normalized),
            "row_count": len(items),
            "items_empty": not items,
            "response_fields": fields,
            "response_fields_sha256": _sha(fields),
            "source_physical_raw_path": (
                f"raw/risk/{source_index}_{call['endpoint']}_{raw_sha}.json"
            ),
            "evidence_path": new_evidence_relative,
            "evidence_bytes": new_evidence_path.stat().st_size,
            "evidence_file_sha256": hashlib.sha256(new_evidence_path.read_bytes()).hexdigest(),
            "evidence_canonical_sha256": evidence["receipt_canonical_sha256"],
        }
    )
    if semantic_empty is not None:
        call["semantic_empty_adjudicated"] = semantic_empty
        call["evidence_kind"] = (
            "risk-semantic-empty-adjudicated/v1"
            if semantic_empty
            else "risk-formal-receipt/v1"
        )
    for descriptor in manifest["declared_files"]:
        if descriptor["path"] == old_raw_relative:
            descriptor["path"] = new_raw_relative
        elif descriptor["path"] == old_evidence_relative:
            descriptor["path"] = new_evidence_relative
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    resigned, _ = _resign_fixture(root)
    return resigned


def _reencoded_snapshot_with_fully_resigned_fixture(
    tmp_path: Path,
) -> tuple[Path, Path]:
    snapshot = tmp_path / "alternate-snapshot" / SNAPSHOT.name
    shutil.copytree(SNAPSHOT, snapshot)
    manifest_path = snapshot / "clean-snapshot-manifest.json"
    snapshot_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(
        json.dumps(snapshot_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt_path = snapshot / "clean-snapshot-receipt.json"
    snapshot_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot_receipt["manifest"].update(
        {
            "bytes": manifest_path.stat().st_size,
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
    )
    unsigned_receipt = dict(snapshot_receipt)
    unsigned_receipt.pop("receipt_canonical_sha256", None)
    snapshot_receipt["receipt_canonical_sha256"] = _sha(unsigned_receipt)
    receipt_path.write_bytes(_canonical_bytes(snapshot_receipt) + b"\n")
    fixture = _copy_fixture(tmp_path / "alternate-fixture")
    fixture_manifest_path = fixture / "manifest.json"
    fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
    fixture_manifest["source_lineage"].update(
        {
            "clean_snapshot_manifest_file_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "clean_snapshot_receipt_file_sha256": hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest(),
            "clean_snapshot_receipt_canonical_sha256": snapshot_receipt[
                "receipt_canonical_sha256"
            ],
            "clean_snapshot_tree_sha256": _sha(_tree_rows(snapshot)),
        }
    )
    fixture_manifest_path.write_bytes(_canonical_bytes(fixture_manifest) + b"\n")
    resigned, _ = _resign_fixture(fixture)
    return snapshot, resigned


def _sqlite_mutated_snapshot_with_fully_resigned_fixture(
    tmp_path: Path,
) -> tuple[Path, Path]:
    snapshot = tmp_path / "sqlite-snapshot" / SNAPSHOT.name
    shutil.copytree(SNAPSHOT, snapshot)
    database = snapshot / "source" / "pit" / "metadata.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        connection.execute(
            "UPDATE fetch_attempts SET http_status = 201 WHERE attempt_sequence = 1"
        )
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    manifest_path = snapshot / "clean-snapshot-manifest.json"
    receipt_path = snapshot / "clean-snapshot-receipt.json"
    audit = json.loads((snapshot / "contamination-audit.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    source_files = _tree_rows(snapshot / "source")
    source_root = _sha(source_files)
    database_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest["source_files"] = source_files
    manifest["source_files_root_sha256"] = source_root
    manifest["source_total_bytes"] = sum(row["bytes"] for row in source_files)
    manifest["sqlite_immutable_probe"]["database_sha256"] = database_sha
    manifest_core = dict(manifest)
    for field in (
        "manifest_canonical_sha256",
        "manifest_content_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
    ):
        manifest_core.pop(field, None)
    manifest["manifest_content_canonical_sha256"] = _sha(manifest_core)
    receipt["source_files_root_sha256"] = source_root
    receipt["manifest_content_canonical_sha256"] = manifest[
        "manifest_content_canonical_sha256"
    ]
    receipt_identity = dict(receipt)
    for field in (
        "receipt_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
        "manifest",
    ):
        receipt_identity.pop(field, None)
    receipt_identity_sha = _sha(receipt_identity)
    receipt["receipt_identity_canonical_sha256"] = receipt_identity_sha
    manifest["receipt_identity_canonical_sha256"] = receipt_identity_sha
    identity = {
        "schema": "clean-source-snapshot-identity/v2",
        "manifest_content_canonical_sha256": manifest[
            "manifest_content_canonical_sha256"
        ],
        "receipt_identity_canonical_sha256": receipt_identity_sha,
        "source_tree_sha256": manifest["source_lineage"]["old_logical_tree_sha256"],
        "declared_files_root_sha256": source_root,
        "contaminated_physical_tree_sha256": audit["actual_files_root_sha256"],
    }
    snapshot_id = _sha(identity)
    manifest["snapshot_id"] = snapshot_id
    receipt["snapshot_id"] = snapshot_id
    manifest_unsigned = dict(manifest)
    manifest_unsigned.pop("manifest_canonical_sha256", None)
    manifest["manifest_canonical_sha256"] = _sha(manifest_unsigned)
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    receipt["manifest"] = {
        "path": "clean-snapshot-manifest.json",
        "bytes": manifest_path.stat().st_size,
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "canonical_sha256": manifest["manifest_canonical_sha256"],
    }
    receipt_unsigned = dict(receipt)
    receipt_unsigned.pop("receipt_canonical_sha256", None)
    receipt["receipt_canonical_sha256"] = _sha(receipt_unsigned)
    receipt_path.write_bytes(_canonical_bytes(receipt) + b"\n")
    resigned_snapshot = snapshot.parent / snapshot_id
    shutil.copytree(snapshot, resigned_snapshot)
    fixture = _copy_fixture(tmp_path / "sqlite-fixture")
    fixture_manifest_path = fixture / "manifest.json"
    fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
    fixture_manifest["source_lineage"].update(
        {
            "clean_snapshot_id": snapshot_id,
            "clean_snapshot_manifest_canonical_sha256": manifest[
                "manifest_canonical_sha256"
            ],
            "clean_snapshot_manifest_file_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "clean_snapshot_receipt_canonical_sha256": receipt[
                "receipt_canonical_sha256"
            ],
            "clean_snapshot_receipt_file_sha256": hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest(),
            "clean_snapshot_tree_sha256": _sha(_tree_rows(resigned_snapshot)),
        }
    )
    fixture_manifest_path.write_bytes(_canonical_bytes(fixture_manifest) + b"\n")
    resigned_fixture, _ = _resign_fixture(fixture)
    return resigned_snapshot, resigned_fixture


def test_trusted_root_verifiers_require_caller_expected() -> None:
    with pytest.raises(TypeError):
        verify_snapshot_v2(SNAPSHOT)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        verify_fixture_v2(
            FIXTURE,
            snapshot_root=SNAPSHOT,
            evidence_use="contaminated_diagnostic_historical",
        )  # type: ignore[call-arg]


def test_pinned_snapshot_and_fixture_tuples_verify() -> None:
    snapshot = verify_snapshot_v2(SNAPSHOT, expected=_snapshot_expected())
    fixture = verify_fixture_v2(
        FIXTURE,
        snapshot_root=SNAPSHOT,
        expected=_fixture_expected(),
        evidence_use="contaminated_diagnostic_historical",
    )
    assert snapshot["payload_file_count"] == 61
    assert fixture["call_count"] == 53
    assert fixture["physical_raw_file_count"] == 50
    assert fixture["snapshot_root_canonical_sha256"] == _snapshot_expected().root_canonical_sha256


def test_expected_objects_reject_unknown_and_missing_fields() -> None:
    payload = _snapshot_expected().to_dict()
    payload["unknown"] = True
    with pytest.raises(TrustedRootError):
        TrustedSnapshotRootV1.from_dict(payload)
    del payload["unknown"]
    del payload["manifest_file_sha256"]
    with pytest.raises(TrustedRootError):
        TrustedSnapshotRootV1.from_dict(payload)


def test_manifest_encoding_change_is_rejected_by_file_sha(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    path = root / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _reject_fixture(root)


def test_full_resign_unknown_field_and_new_content_address_is_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["attacker_re_signed"] = True
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    resigned, _ = _resign_fixture(root)
    assert resigned.name != FIXTURE.name
    _assert_fixture_identity_resigned(resigned)
    _reject_fixture(resigned)


def test_full_resign_snapshot_lineage_transplant_is_rejected(tmp_path: Path) -> None:
    snapshot, resigned = _reencoded_snapshot_with_fully_resigned_fixture(tmp_path)
    assert resigned.name != FIXTURE.name
    _assert_snapshot_identity_resigned(snapshot)
    _assert_fixture_identity_resigned(resigned)
    _reject_fixture(resigned, snapshot_root=snapshot)


def test_full_resign_50_to_30_role_laundering_is_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    replacement = manifest["calls"][15]["raw_path"]
    removed = {manifest["calls"][index]["raw_path"] for index in range(16, 36)}
    for index in range(16, 36):
        manifest["calls"][index]["raw_path"] = replacement
    manifest["declared_files"] = [row for row in manifest["declared_files"] if row["path"] not in removed]
    manifest["physical_raw_summary"] = {
        "total": 30,
        "immutable_source_lineage": 11,
        "risk_formal_lineage": 19,
    }
    for relative in removed:
        (root / relative).unlink()
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    resigned, _ = _resign_fixture(root)
    receipt_path = resigned / "fixture-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["physical_raw_file_count"] = 30
    receipt_unsigned = dict(receipt)
    receipt_unsigned.pop("receipt_canonical_sha256", None)
    receipt["receipt_canonical_sha256"] = _sha(receipt_unsigned)
    receipt_path.write_bytes(_canonical_bytes(receipt) + b"\n")
    _assert_fixture_identity_resigned(resigned)
    _reject_fixture(resigned)


def test_full_resign_raw_and_formal_receipt_row_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    resigned = _full_resign_risk_raw(
        root,
        36,
        lambda envelope: envelope["data"]["items"][0].__setitem__(1, "tampered-name"),
    )
    assert resigned.name != FIXTURE.name
    _assert_fixture_identity_resigned(resigned)
    _reject_fixture(resigned)


def test_full_resign_sqlite_row_snapshot_and_fixture_lineage_is_rejected(
    tmp_path: Path,
) -> None:
    snapshot, fixture = _sqlite_mutated_snapshot_with_fully_resigned_fixture(tmp_path)
    assert snapshot.name != SNAPSHOT.name
    assert fixture.name != FIXTURE.name
    _assert_snapshot_identity_resigned(snapshot)
    _assert_fixture_identity_resigned(fixture)
    _reject_fixture(fixture, snapshot_root=snapshot)


@pytest.mark.parametrize("attack", ["unlicensed-empty", "after-cutoff-row"])
def test_full_resign_unlicensed_empty_or_cutoff_row_is_rejected(
    tmp_path: Path,
    attack: str,
) -> None:
    root = _copy_fixture(tmp_path)
    if attack == "unlicensed-empty":
        resigned = _full_resign_risk_raw(
            root,
            14,
            lambda envelope: envelope["data"].update(items=[]),
            semantic_empty=True,
        )
    else:
        resigned = _full_resign_risk_raw(
            root,
            36,
            lambda envelope: envelope["data"]["items"][0].__setitem__(2, "20260711"),
        )
    assert resigned.name != FIXTURE.name
    _assert_fixture_identity_resigned(resigned)
    _reject_fixture(resigned)


def test_nested_snapshot_tuple_must_be_the_same_caller_tuple() -> None:
    fixture = _fixture_expected().to_dict()
    fixture["snapshot"]["directory_id"] = "b" * 64
    fixture["snapshot"]["root_canonical_sha256"] = _sha(
        {key: value for key, value in fixture["snapshot"].items() if key != "root_canonical_sha256"}
    )
    fixture["root_canonical_sha256"] = _sha(
        {key: value for key, value in fixture.items() if key != "root_canonical_sha256"}
    )
    expected = TrustedFixtureRootV1.from_dict(fixture)
    _reject_fixture(FIXTURE, expected)
