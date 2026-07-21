import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.research_development_payload_fixture import (
    DevelopmentPayloadFixtureError,
    fixture_producer_source_sha256,
    open_development_payload_loader,
    publish_development_payload_fixture,
    verify_development_payload_fixture,
)


CONTRACT = "c" * 64


def _canonical_sha256(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _manifest(root: Path, start: str, end: str, sequence: int) -> dict:
    metadata = root / "metadata.sqlite3"
    connection = sqlite3.connect(metadata)
    try:
        connection.execute(
            """
            CREATE TABLE trade_sessions (
                exchange TEXT NOT NULL,
                cal_date TEXT NOT NULL,
                is_open INTEGER NOT NULL,
                pretrade_date TEXT,
                receipt_dataset TEXT NOT NULL,
                receipt_partition TEXT NOT NULL,
                PRIMARY KEY (exchange, cal_date)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO trade_sessions (
                exchange, cal_date, is_open, pretrade_date, receipt_dataset, receipt_partition
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("SSE", start, 1, "2021-12-31", "trade_cal", f"SSE:{start}:{end}"),
                ("SSE", end, 1, start, "trade_cal", f"SSE:{start}:{end}"),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    payload = {
        "schema_version": "audited-pit-universe/v5",
        "artifact_role": "development_only",
        "artifact_root_sha256": f"{sequence:x}" * 64,
        "coverage_audit_sha256": f"{sequence + 1:x}" * 64,
        "coverage": {"start_date": start, "end_date": end},
        "temporal_binding": {
            "contract_sha256": CONTRACT,
            "role": "development",
            "start_date": start,
            "end_date": end,
            "promotion_eligible": False,
        },
        "market_generations": {"root_sha256": f"{sequence + 2:x}" * 64},
        "stock_generation": {"lineage_sha256": f"{sequence + 3:x}" * 64},
        "producer_code_sha256": f"{sequence + 4:x}" * 64,
        "final_oos_eligible": False,
    }
    payload["manifest_sha256"] = _canonical_sha256(payload)
    (root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return payload


def _composite_root(first: dict, second: dict) -> str:
    return _canonical_sha256(
        {
            "schema_version": "research-composite-universe/v1",
            "coverage": {"start_date": "2022-01-04", "end_date": "2023-12-29"},
            "temporal_role": "development",
            "temporal_contract_sha256": CONTRACT,
            "permitted_boundary_gaps": [
                {"previous_end_date": "2022-12-30", "next_start_date": "2023-01-03"}
            ],
            "segments": [
                {
                    "sequence": sequence,
                    "coverage": source["coverage"],
                    "artifact_root_sha256": source["artifact_root_sha256"],
                    "coverage_audit_sha256": source["coverage_audit_sha256"],
                    "temporal_contract_sha256": CONTRACT,
                    "temporal_role": "development",
                    "artifact_manifest_sha256": source["manifest_sha256"],
                    "market_generation_root_sha256": source["market_generations"]["root_sha256"],
                    "stock_generation_lineage_sha256": source["stock_generation"]["lineage_sha256"],
                }
                for sequence, source in enumerate((first, second), 1)
            ],
        }
    )


def _source_descriptor(path: Path, first_root: Path, first: dict, second_root: Path, second: dict) -> None:
    path.write_bytes(
        _canonical_bytes(
            {
                "schema_version": "research-composite-universe-descriptor/v1",
                "segments": [
                    {
                        "path": str(first_root / "metadata.sqlite3"),
                        "expected_artifact_root_sha256": first["artifact_root_sha256"],
                        "expected_coverage_audit_sha256": first["coverage_audit_sha256"],
                        "expected_temporal_contract_sha256": CONTRACT,
                        "expected_artifact_manifest_sha256": first["manifest_sha256"],
                        "expected_temporal_role": "development",
                    },
                    {
                        "path": str(second_root / "metadata.sqlite3"),
                        "expected_artifact_root_sha256": second["artifact_root_sha256"],
                        "expected_coverage_audit_sha256": second["coverage_audit_sha256"],
                        "expected_temporal_contract_sha256": CONTRACT,
                        "expected_artifact_manifest_sha256": second["manifest_sha256"],
                        "expected_temporal_role": "development",
                    },
                ],
                "permitted_boundary_gaps": [
                    {"previous_end_date": "2022-12-30", "next_start_date": "2023-01-03"}
                ],
            }
        )
        + b"\n"
    )


def _anchor(root: Path, manifest: dict, sequence: int) -> dict:
    metadata = root / "metadata.sqlite3"
    manifest_path = root / "manifest.json"
    metadata_raw = metadata.read_bytes()
    manifest_raw = manifest_path.read_bytes()
    return {
        "sequence": sequence,
        "coverage": manifest["coverage"],
        "metadata_path": str(metadata.resolve()),
        "metadata_bytes": len(metadata_raw),
        "metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_bytes": len(manifest_raw),
        "manifest_file_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "artifact_root_sha256": manifest["artifact_root_sha256"],
        "coverage_audit_sha256": manifest["coverage_audit_sha256"],
        "artifact_manifest_sha256": manifest["manifest_sha256"],
        "market_generation_root_sha256": manifest["market_generations"]["root_sha256"],
        "stock_generation_lineage_sha256": manifest["stock_generation"]["lineage_sha256"],
        "producer_code_sha256": manifest["producer_code_sha256"],
        "temporal_contract_sha256": CONTRACT,
        "temporal_role": "development",
    }


def _source_fixture(tmp_path: Path) -> tuple[Path, str, list[dict]]:
    first_root = tmp_path / "source-2022"
    second_root = tmp_path / "source-2023"
    first_root.mkdir()
    second_root.mkdir()
    first = _manifest(first_root, "2022-01-04", "2022-12-30", 1)
    second = _manifest(second_root, "2023-01-03", "2023-12-29", 5)
    descriptor = tmp_path / "source-descriptor.json"
    _source_descriptor(descriptor, first_root, first, second_root, second)
    return descriptor, _composite_root(first, second), [
        _anchor(first_root, first, 1),
        _anchor(second_root, second, 2),
    ]


def _source_descriptor_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _publish(parent: Path, descriptor: Path, composite_root: str, anchors: list[dict]) -> dict:
    return publish_development_payload_fixture(
        parent,
        source_descriptor_path=descriptor,
        expected_composite_root_sha256=composite_root,
        expected_temporal_contract_sha256=CONTRACT,
        producer_source_sha256=fixture_producer_source_sha256(),
        expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
        expected_source_anchors=anchors,
        expected_workspace_root=descriptor.parent,
    )


def _verify(published: dict, composite_root: str, descriptor: Path, anchors: list[dict]) -> dict:
    return verify_development_payload_fixture(
        published["path"],
        expected_fixture_id=published["fixture_id"],
        expected_manifest_file_sha256=published["manifest_file_sha256"],
        expected_receipt_file_sha256=published["receipt_file_sha256"],
        expected_composite_root_sha256=composite_root,
        expected_temporal_contract_sha256=CONTRACT,
        expected_producer_source_sha256=fixture_producer_source_sha256(),
        expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
        expected_source_anchors=anchors,
        expected_workspace_root=descriptor.parent,
    )


def _resign_fixture(root: Path, descriptor: dict) -> dict:
    """Model an attacker who recomputes every internal hash and content address."""
    descriptor_raw = _canonical_bytes(descriptor) + b"\n"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["payload_descriptor_file_sha256"] = hashlib.sha256(descriptor_raw).hexdigest()
    manifest["payload_descriptor_canonical_sha256"] = _canonical_sha256(descriptor)
    manifest["source_segments"] = descriptor["source_segments"]
    manifest["source_segments_root_sha256"] = descriptor["source_segments_root_sha256"]
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"fixture_id", "manifest_canonical_sha256"}
    }
    fixture_id = _canonical_sha256(body)
    manifest["fixture_id"] = fixture_id
    manifest["manifest_canonical_sha256"] = _canonical_sha256(
        {key: value for key, value in manifest.items() if key != "manifest_canonical_sha256"}
    )
    manifest_raw = _canonical_bytes(manifest) + b"\n"
    tree = [
        {"path": "manifest.json", "sha256": hashlib.sha256(manifest_raw).hexdigest()},
        {"path": "payload-descriptor.json", "sha256": hashlib.sha256(descriptor_raw).hexdigest()},
    ]
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    receipt["fixture_id"] = fixture_id
    receipt["manifest_file_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    receipt["manifest_canonical_sha256"] = manifest["manifest_canonical_sha256"]
    receipt["payload_files_root_sha256"] = _canonical_sha256(tree)
    receipt["receipt_canonical_sha256"] = _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
    )
    receipt_raw = _canonical_bytes(receipt) + b"\n"
    resigned = root.parent / fixture_id
    shutil.copytree(root, resigned)
    (resigned / "payload-descriptor.json").write_bytes(descriptor_raw)
    (resigned / "manifest.json").write_bytes(manifest_raw)
    (resigned / "receipt.json").write_bytes(receipt_raw)
    return {
        "path": resigned,
        "fixture_id": fixture_id,
        "manifest_file_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "receipt_file_sha256": hashlib.sha256(receipt_raw).hexdigest(),
    }

def test_publish_and_verify_fixture_binds_only_two_development_segments(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)

    verified = _verify(published, composite_root, descriptor, anchors)
    assert verified["temporal_role"] == "development"
    assert verified["date_bounds"] == {"start_date": "2022-01-04", "end_date": "2023-12-29"}
    assert verified["composite_root_sha256"] == composite_root
    assert [segment["coverage"] for segment in verified["segments"]] == [
        {"start_date": "2022-01-04", "end_date": "2022-12-30"},
        {"start_date": "2023-01-03", "end_date": "2023-12-29"},
    ]


def test_publish_rejects_a_tail_or_non_development_segment(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    second_manifest = Path(payload["segments"][1]["path"]).parent / "manifest.json"
    manifest = json.loads(second_manifest.read_text(encoding="utf-8"))
    manifest["coverage"] = {"start_date": "2024-01-02", "end_date": "2024-01-05"}
    manifest["temporal_binding"]["start_date"] = "2024-01-02"
    manifest["temporal_binding"]["end_date"] = "2024-01-05"
    manifest["manifest_sha256"] = _canonical_sha256(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    second_manifest.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    payload["segments"][1]["expected_artifact_manifest_sha256"] = manifest["manifest_sha256"]
    descriptor.write_bytes(_canonical_bytes(payload) + b"\n")

    with pytest.raises(DevelopmentPayloadFixtureError, match="temporal evidence"):
        _publish(tmp_path / "published", descriptor, composite_root, anchors)


def test_verifier_rejects_sqlite_byte_tampering(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    source = Path(published["source_segments"][0]["metadata_path"])
    source.write_bytes(b"changed")

    with pytest.raises(DevelopmentPayloadFixtureError, match="metadata"):
        _verify(published, composite_root, descriptor, anchors)


@pytest.mark.parametrize("protected_name", ["source_mirror", "source_mirrors", "current_pool_runs"])
def test_publish_rejects_protected_current_pool_or_source_mirror_lineage(tmp_path, protected_name):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    source_root = Path(payload["segments"][0]["path"]).parent
    protected_parent = tmp_path / protected_name
    protected_parent.mkdir()
    protected_root = protected_parent / source_root.name
    source_root.rename(protected_root)
    payload["segments"][0]["path"] = str(protected_root / "metadata.sqlite3")
    anchors[0]["metadata_path"] = str((protected_root / "metadata.sqlite3").resolve())
    anchors[0]["manifest_path"] = str((protected_root / "manifest.json").resolve())
    descriptor.write_bytes(_canonical_bytes(payload) + b"\n")

    with pytest.raises(DevelopmentPayloadFixtureError, match="protected"):
        _publish(tmp_path / "published", descriptor, composite_root, anchors)


def test_verifier_requires_caller_trusted_composite_root(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)

    with pytest.raises(DevelopmentPayloadFixtureError, match="external trusted root"):
        verify_development_payload_fixture(
            published["path"],
            expected_fixture_id=published["fixture_id"],
            expected_manifest_file_sha256=published["manifest_file_sha256"],
            expected_receipt_file_sha256=published["receipt_file_sha256"],
            expected_composite_root_sha256="f" * 64,
            expected_temporal_contract_sha256=CONTRACT,
            expected_producer_source_sha256=fixture_producer_source_sha256(),
            expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
            expected_source_anchors=anchors,
            expected_workspace_root=descriptor.parent,
        )


def test_verifier_rejects_fully_resigned_unknown_payload_field(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    root = Path(published["path"])
    payload = json.loads((root / "payload-descriptor.json").read_text(encoding="utf-8"))
    payload["untrusted_resigned_field"] = "must-not-pass"
    resigned = _resign_fixture(root, payload)

    with pytest.raises(DevelopmentPayloadFixtureError, match="payload schema"):
        verify_development_payload_fixture(
            resigned["path"],
            expected_fixture_id=resigned["fixture_id"],
            expected_manifest_file_sha256=resigned["manifest_file_sha256"],
            expected_receipt_file_sha256=resigned["receipt_file_sha256"],
            expected_composite_root_sha256=composite_root,
            expected_temporal_contract_sha256=CONTRACT,
            expected_producer_source_sha256=fixture_producer_source_sha256(),
            expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
            expected_source_anchors=anchors,
            expected_workspace_root=descriptor.parent,
        )


def test_verifier_rejects_resigned_receipt_without_caller_receipt_anchor(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    root = Path(published["path"])
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    receipt["created_at_utc"] = "2099-01-01T00:00:00+00:00"
    receipt["receipt_canonical_sha256"] = _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
    )
    (root / "receipt.json").write_bytes(_canonical_bytes(receipt) + b"\n")

    with pytest.raises(DevelopmentPayloadFixtureError, match="receipt file SHA"):
        _verify(published, composite_root, descriptor, anchors)


def test_verifier_rejects_unprotected_path_alias_after_full_resign(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    alias_root = tmp_path / "ordinary-alias"
    shutil.copytree(Path(anchors[0]["metadata_path"]).parent, alias_root)
    root = Path(published["path"])
    payload = json.loads((root / "payload-descriptor.json").read_text(encoding="utf-8"))
    payload["source_segments"][0]["metadata_path"] = str((alias_root / "metadata.sqlite3").resolve())
    payload["source_segments"][0]["manifest_path"] = str((alias_root / "manifest.json").resolve())
    payload["source_segments_root_sha256"] = _canonical_sha256(payload["source_segments"])
    resigned = _resign_fixture(root, payload)

    with pytest.raises(DevelopmentPayloadFixtureError, match="caller trusted anchors"):
        verify_development_payload_fixture(
            resigned["path"],
            expected_fixture_id=resigned["fixture_id"],
            expected_manifest_file_sha256=resigned["manifest_file_sha256"],
            expected_receipt_file_sha256=resigned["receipt_file_sha256"],
            expected_composite_root_sha256=composite_root,
            expected_temporal_contract_sha256=CONTRACT,
            expected_producer_source_sha256=fixture_producer_source_sha256(),
            expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
            expected_source_anchors=anchors,
            expected_workspace_root=descriptor.parent,
        )


def test_loader_reads_only_one_development_segment_and_rejects_boundaries(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    loader = open_development_payload_loader(
        published["path"],
        expected_fixture_id=published["fixture_id"],
        expected_manifest_file_sha256=published["manifest_file_sha256"],
        expected_receipt_file_sha256=published["receipt_file_sha256"],
        expected_composite_root_sha256=composite_root,
        expected_temporal_contract_sha256=CONTRACT,
        expected_producer_source_sha256=fixture_producer_source_sha256(),
        expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
        expected_source_anchors=anchors,
        expected_workspace_root=descriptor.parent,
    )

    rows = loader.read_trade_sessions("2022-01-04", "2022-12-30")
    assert rows == [
        {
            "exchange": "SSE",
            "cal_date": "2022-01-04",
            "is_open": 1,
            "pretrade_date": "2021-12-31",
            "receipt_dataset": "trade_cal",
            "receipt_partition": "SSE:2022-01-04:2022-12-30",
        },
        {
            "exchange": "SSE",
            "cal_date": "2022-12-30",
            "is_open": 1,
            "pretrade_date": "2022-01-04",
            "receipt_dataset": "trade_cal",
            "receipt_partition": "SSE:2022-01-04:2022-12-30",
        },
    ]
    assert list(rows[0]) == [
        "exchange",
        "cal_date",
        "is_open",
        "pretrade_date",
        "receipt_dataset",
        "receipt_partition",
    ]
    with pytest.raises(DevelopmentPayloadFixtureError, match="temporal boundary"):
        loader.read_trade_sessions("2022-12-30", "2023-01-03")
    with pytest.raises(DevelopmentPayloadFixtureError, match="temporal boundary"):
        loader.read_trade_sessions("2024-01-02", "2024-01-02")


def test_loader_rechecks_sqlite_anchor_before_reading(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    loader = open_development_payload_loader(
        published["path"],
        expected_fixture_id=published["fixture_id"],
        expected_manifest_file_sha256=published["manifest_file_sha256"],
        expected_receipt_file_sha256=published["receipt_file_sha256"],
        expected_composite_root_sha256=composite_root,
        expected_temporal_contract_sha256=CONTRACT,
        expected_producer_source_sha256=fixture_producer_source_sha256(),
        expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
        expected_source_anchors=anchors,
        expected_workspace_root=descriptor.parent,
    )
    Path(anchors[0]["metadata_path"]).write_bytes(b"tampered-after-loader-open")

    with pytest.raises(DevelopmentPayloadFixtureError, match="differs from the verified anchor"):
        loader.read_trade_sessions("2022-01-04", "2022-01-04")


def test_publisher_rejects_anchors_outside_caller_workspace(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    outside = tmp_path / "separate-workspace"
    outside.mkdir()

    with pytest.raises(DevelopmentPayloadFixtureError, match="outside the caller trusted workspace"):
        publish_development_payload_fixture(
            tmp_path / "published",
            source_descriptor_path=descriptor,
            expected_composite_root_sha256=composite_root,
            expected_temporal_contract_sha256=CONTRACT,
            producer_source_sha256=fixture_producer_source_sha256(),
            expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
            expected_source_anchors=anchors,
            expected_workspace_root=outside,
        )


def test_publisher_rejects_protected_parent_before_creating_output(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    protected_parent = tmp_path / "auto-iter-037-h1-hold3-development" / "fixture-output"

    with pytest.raises(DevelopmentPayloadFixtureError, match="protected"):
        _publish(protected_parent, descriptor, composite_root, anchors)
    assert not protected_parent.exists()


def test_verifier_rejects_fixture_root_under_protected_lineage(tmp_path):
    descriptor, composite_root, anchors = _source_fixture(tmp_path)
    published = _publish(tmp_path / "published", descriptor, composite_root, anchors)
    root = Path(published["path"])
    protected_parent = tmp_path / "auto-iter-036-h1-hold3-development"
    protected_parent.mkdir()
    protected_root = protected_parent / root.name
    root.rename(protected_root)

    with pytest.raises(DevelopmentPayloadFixtureError, match="protected"):
        verify_development_payload_fixture(
            protected_root,
            expected_fixture_id=published["fixture_id"],
            expected_manifest_file_sha256=published["manifest_file_sha256"],
            expected_receipt_file_sha256=published["receipt_file_sha256"],
            expected_composite_root_sha256=composite_root,
            expected_temporal_contract_sha256=CONTRACT,
            expected_producer_source_sha256=fixture_producer_source_sha256(),
            expected_source_descriptor_file_sha256=_source_descriptor_sha256(descriptor),
            expected_source_anchors=anchors,
            expected_workspace_root=descriptor.parent,
        )
