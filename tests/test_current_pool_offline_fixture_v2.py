from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from app.current_pool_offline_fixture_v2 import (
    AS_OF,
    EVIDENCE_USE,
    INVALID_AUDIT_CANONICAL_SHA256,
    INVALID_AUDIT_FILE_SHA256,
    INVALID_RUN_ID,
    INVALID_RUN_TREE_SHA256,
    OfflineFixtureV2Error,
    RISK_EMPTY_INDICES,
    RISK_SEGMENT_SCHEMA,
    SOURCE_EMPTY_INDICES,
    SOURCE_SEGMENT_SCHEMA,
    _verify_clean_snapshot,
    compile_current_pool_offline_fixture_v2,
    preflight_current_pool_offline_fixture_v2,
    replay_current_pool_offline_fixture_v2_calls,
)


ROOT = Path(__file__).resolve().parents[1]
CLEAN = (
    ROOT
    / "data"
    / "research_fixtures"
    / "clean_source_snapshots"
    / "f5b2f91c2a500935f046f40023f0b9301bce4871f5d7528cebe93172b6ebbab7"
)
OLD_CONTAMINATED = (
    ROOT
    / "data"
    / "research_fixtures"
    / "source_mirrors"
    / "089d14d1bef5daf597753253f3aba75ea16a9bd3a5587fc9fc2b421b919f7d0a"
)
INVALID_RUN = (
    ROOT
    / "data"
    / "research_artifacts"
    / "current_pool_risk_evidence_runs"
    / INVALID_RUN_ID
)
INVALID_AUDIT = (
    ROOT
    / "data"
    / "research_artifacts"
    / "current_pool_risk_evidence_audits"
    / f"{INVALID_AUDIT_CANONICAL_SHA256}.json"
)


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_signed(path: Path, payload: dict, field: str) -> dict:
    unsigned = dict(payload)
    unsigned.pop(field, None)
    payload[field] = _sha(unsigned)
    path.write_bytes(_canonical_bytes(payload) + b"\n")
    return payload


@pytest.fixture(scope="session")
def compiled_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output_parent = tmp_path_factory.mktemp("offline-fixture-v2") / "published"
    result = compile_current_pool_offline_fixture_v2(
        clean_snapshot_root=CLEAN,
        invalid_run_root=INVALID_RUN,
        invalid_audit_path=INVALID_AUDIT,
        output_parent=output_parent,
    )
    return result


def _copy_fixture(tmp_path: Path, compiled_fixture: dict) -> Path:
    source = Path(compiled_fixture["fixture_root"])
    target = tmp_path / source.name
    shutil.copytree(source, target)
    return target


def _copy_clean_snapshot(tmp_path: Path) -> Path:
    target = tmp_path / CLEAN.name
    shutil.copytree(CLEAN, target)
    return target


def _resign_clean_snapshot(root: Path) -> None:
    audit_path = root / "contamination-audit.json"
    audit = _write_signed(
        audit_path,
        json.loads(audit_path.read_text(encoding="utf-8")),
        "audit_canonical_sha256",
    )
    manifest_path = root / "clean-snapshot-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contamination_audit"] = {
        "path": audit_path.name,
        "bytes": audit_path.stat().st_size,
        "sha256": _file_sha(audit_path),
        "canonical_sha256": audit["audit_canonical_sha256"],
    }
    manifest = _write_signed(
        manifest_path, manifest, "manifest_canonical_sha256"
    )
    receipt_path = root / "clean-snapshot-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["snapshot_id"] = manifest["snapshot_id"]
    receipt["contamination_audit_canonical_sha256"] = audit[
        "audit_canonical_sha256"
    ]
    receipt["manifest"] = {
        "path": manifest_path.name,
        "bytes": manifest_path.stat().st_size,
        "sha256": _file_sha(manifest_path),
        "canonical_sha256": manifest["manifest_canonical_sha256"],
    }
    _write_signed(receipt_path, receipt, "receipt_canonical_sha256")


def _resign_fixture(root: Path) -> tuple[Path, str]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    calls = manifest["calls"]
    for call in calls:
        raw = root / call["raw_path"]
        if raw.is_file():
            call["raw_bytes"] = raw.stat().st_size
            call["raw_sha256"] = _file_sha(raw)
        evidence = root / call["evidence_path"]
        if evidence.is_file():
            call["evidence_bytes"] = evidence.stat().st_size
            call["evidence_file_sha256"] = _file_sha(evidence)
    adjudication_path = root / manifest["risk_adjudication"]["path"]
    if adjudication_path.is_file():
        manifest["risk_adjudication"]["bytes"] = adjudication_path.stat().st_size
        manifest["risk_adjudication"]["sha256"] = _file_sha(adjudication_path)
        adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
        manifest["risk_adjudication"]["canonical_sha256"] = adjudication[
            "adjudication_canonical_sha256"
        ]
    for descriptor in manifest["declared_files"]:
        artifact = root / descriptor["path"]
        descriptor["bytes"] = artifact.stat().st_size
        descriptor["sha256"] = _file_sha(artifact)
    manifest["segments"][0]["calls_root_sha256"] = _sha(calls[:14])
    manifest["segments"][1]["calls_root_sha256"] = _sha(calls[14:])
    manifest["calls_root_sha256"] = _sha(calls)
    manifest["declared_files_root_sha256"] = _sha(manifest["declared_files"])
    content = dict(manifest)
    content.pop("canonical_sha256", None)
    content.pop("content_canonical_sha256", None)
    content.pop("fixture_id", None)
    manifest["content_canonical_sha256"] = _sha(content)
    identity = {
        "schema": "current-pool-offline-fixture-identity/v2",
        "content_canonical_sha256": manifest["content_canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "source_lineage": manifest["source_lineage"],
        "risk_adjudication": manifest["risk_adjudication"],
    }
    manifest["fixture_id"] = _sha(identity)
    _write_signed(manifest_path, manifest, "canonical_sha256")
    receipt_path = root / "fixture-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["fixture_id"] = manifest["fixture_id"]
    receipt["manifest_file_sha256"] = _file_sha(manifest_path)
    receipt["manifest_canonical_sha256"] = manifest["canonical_sha256"]
    receipt["declared_files_root_sha256"] = manifest["declared_files_root_sha256"]
    _write_signed(receipt_path, receipt, "receipt_canonical_sha256")
    resigned_root = root.parent / manifest["fixture_id"]
    if resigned_root != root:
        shutil.copytree(root, resigned_root)
    return resigned_root, _file_sha(resigned_root / "manifest.json")


def _resign_evidence_and_repoint_call(
    root: Path, source_index: int, mutator
) -> tuple[Path, str]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    call = manifest["calls"][source_index]
    old_relative = call["evidence_path"]
    old_path = root / old_relative
    evidence = json.loads(old_path.read_text(encoding="utf-8"))
    mutator(evidence)
    canonical_field = (
        "evidence_canonical_sha256"
        if source_index < 14
        else "receipt_canonical_sha256"
    )
    unsigned = dict(evidence)
    unsigned.pop(canonical_field, None)
    evidence[canonical_field] = _sha(unsigned)
    new_relative = (
        f"evidence/source/{source_index:02d}-{evidence[canonical_field]}.json"
        if source_index < 14
        else f"evidence/risk/{evidence[canonical_field]}.json"
    )
    new_path = root / new_relative
    new_path.write_bytes(_canonical_bytes(evidence) + b"\n")
    old_path.unlink()
    call["evidence_path"] = new_relative
    call["evidence_canonical_sha256"] = evidence[canonical_field]
    for descriptor in manifest["declared_files"]:
        if descriptor["path"] == old_relative:
            descriptor["path"] = new_relative
            break
    _write_signed(manifest_path, manifest, "canonical_sha256")
    return _resign_fixture(root)


def _reject(
    root: Path,
    manifest_sha: str | tuple[Path, str],
    *,
    evidence_use: str = EVIDENCE_USE,
) -> None:
    if isinstance(manifest_sha, tuple):
        root, manifest_sha = manifest_sha
    with pytest.raises(OfflineFixtureV2Error, match="offline fixture v2 rejected"):
        preflight_current_pool_offline_fixture_v2(
            fixture_root=root,
            expected_manifest_sha256=manifest_sha,
            evidence_use=evidence_use,
        )


def test_clean_snapshot_preserves_exact_allowlist_and_pollution_audit() -> None:
    manifest = json.loads((CLEAN / "clean-snapshot-manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((CLEAN / "contamination-audit.json").read_text(encoding="utf-8"))
    source_files = [path for path in (CLEAN / "source").rglob("*") if path.is_file()]
    assert len(source_files) == 56
    assert not any(path.name.endswith(("-wal", "-shm", "-journal")) for path in source_files)
    assert manifest["copy_parity_count"] == 56
    assert audit["status"] == "contaminated_by_sqlite_sidecars"
    assert audit["declared_file_count"] == 56
    assert audit["actual_file_count"] == 58
    assert {row["path"] for row in audit["extra_files"]} == {
        "pit/metadata.sqlite3-shm",
        "pit/metadata.sqlite3-wal",
    }


def test_compiler_rejects_old_contaminated_mirror(tmp_path: Path) -> None:
    with pytest.raises(OfflineFixtureV2Error):
        compile_current_pool_offline_fixture_v2(
            clean_snapshot_root=OLD_CONTAMINATED,
            invalid_run_root=INVALID_RUN,
            invalid_audit_path=INVALID_AUDIT,
            output_parent=tmp_path / "out",
        )


def test_compiler_rejects_an_existing_single_writer_lock(tmp_path: Path) -> None:
    output = tmp_path / "published"
    output.mkdir()
    lock = output / ".current-pool-offline-fixture-v2.lock"
    lock.write_text("occupied\n", encoding="utf-8")
    with pytest.raises(OfflineFixtureV2Error, match="publisher lock already exists"):
        compile_current_pool_offline_fixture_v2(
            clean_snapshot_root=CLEAN,
            invalid_run_root=INVALID_RUN,
            invalid_audit_path=INVALID_AUDIT,
            output_parent=output,
        )
    assert list(output.iterdir()) == [lock]


def test_fully_resigned_clean_audit_mutation_is_rejected(tmp_path: Path) -> None:
    root = _copy_clean_snapshot(tmp_path)
    audit_path = root / "contamination-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["extra_files"][0]["sha256"] = "0" * 64
    audit["source_after"]["files_root_sha256"] = "1" * 64
    _write_signed(audit_path, audit, "audit_canonical_sha256")
    _resign_clean_snapshot(root)
    with pytest.raises(OfflineFixtureV2Error):
        _verify_clean_snapshot(root)


def test_fully_resigned_clean_receipt_mutation_is_rejected(tmp_path: Path) -> None:
    root = _copy_clean_snapshot(tmp_path)
    receipt_path = root / "clean-snapshot-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["copy_parity_count"] = 55
    receipt["network_calls"] = 1
    _write_signed(receipt_path, receipt, "receipt_canonical_sha256")
    with pytest.raises(OfflineFixtureV2Error):
        _verify_clean_snapshot(root)


def test_fully_resigned_clean_snapshot_identity_alias_is_rejected(
    tmp_path: Path,
) -> None:
    alias = "a" * 64
    alias_root = tmp_path / alias
    shutil.copytree(CLEAN, alias_root)
    root = alias_root
    manifest_path = root / "clean-snapshot-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_id"] = alias
    _write_signed(manifest_path, manifest, "manifest_canonical_sha256")
    _resign_clean_snapshot(root)
    with pytest.raises(OfflineFixtureV2Error):
        _verify_clean_snapshot(alias_root)


def test_clean_snapshot_root_injection_is_rejected(tmp_path: Path) -> None:
    root = _copy_clean_snapshot(tmp_path)
    (root / "unbound.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(OfflineFixtureV2Error):
        _verify_clean_snapshot(root)


def test_compile_preflight_and_replay_exact_53_calls(compiled_fixture: dict) -> None:
    from app import current_pool_offline_replay as adapter

    verified = preflight_current_pool_offline_fixture_v2(
        fixture_root=compiled_fixture["fixture_root"],
        expected_manifest_sha256=compiled_fixture["manifest_file_sha256"],
        evidence_use=EVIDENCE_USE,
    )
    replayed = replay_current_pool_offline_fixture_v2_calls(
        fixture_root=compiled_fixture["fixture_root"],
        expected_manifest_sha256=compiled_fixture["manifest_file_sha256"],
        evidence_use=EVIDENCE_USE,
    )
    assert [row["source_index"] for row in replayed] == list(range(53))
    assert verified["segment_counts"] == {
        SOURCE_SEGMENT_SCHEMA: 14,
        RISK_SEGMENT_SCHEMA: 39,
    }
    assert verified["source_empty_count"] == 4
    assert verified["risk_semantic_empty_count"] == 20
    assert verified["eligible_pool_count"] == 0
    assert verified["production_recommendation_eligible"] is False
    wrapped = adapter.replay_current_pool_offline_fixture_v2_calls(
        fixture_root=compiled_fixture["fixture_root"],
        expected_manifest_sha256=compiled_fixture["manifest_file_sha256"],
        evidence_use=EVIDENCE_USE,
    )
    assert [row["source_index"] for row in wrapped] == list(range(53))


def test_fixture_preserves_frozen_50_physical_raw_lineage(compiled_fixture: dict) -> None:
    manifest = json.loads(
        (Path(compiled_fixture["fixture_root"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    raw_paths = {call["raw_path"] for call in manifest["calls"]}
    declared_raw_paths = {
        row["path"]
        for row in manifest["declared_files"]
        if row["path"].startswith("raw/")
    }
    assert len(raw_paths) == 50
    assert len({path for path in raw_paths if path.startswith("raw/source-pit/")}) == 11
    assert len({path for path in raw_paths if path.startswith("raw/source-risk/")}) == 39
    assert declared_raw_paths == raw_paths


def test_fully_resigned_fixture_identity_alias_is_rejected(
    tmp_path: Path, compiled_fixture: dict
) -> None:
    alias = "b" * 64
    root = tmp_path / alias
    shutil.copytree(Path(compiled_fixture["fixture_root"]), root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_id"] = alias
    _write_signed(manifest_path, manifest, "canonical_sha256")
    receipt_path = root / "fixture-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["fixture_id"] = alias
    receipt["manifest_file_sha256"] = _file_sha(manifest_path)
    receipt["manifest_canonical_sha256"] = manifest["canonical_sha256"]
    _write_signed(receipt_path, receipt, "receipt_canonical_sha256")
    _reject(root, _file_sha(manifest_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("call_count", 52),
        ("segment_count", 3),
        ("single_writer", False),
        ("atomic_publish", False),
        ("network_calls", False),
    ],
)
def test_fully_resigned_receipt_contract_mutation_is_rejected(
    tmp_path: Path, compiled_fixture: dict, field: str, value
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    receipt_path = root / "fixture-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    _write_signed(receipt_path, receipt, "receipt_canonical_sha256")
    _reject(root, compiled_fixture["manifest_file_sha256"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "success"),
        ("historical_v1_run_status", "complete"),
        ("invalid_audit_file_sha256", "0" * 64),
        ("eligible_pool_count", 1),
        ("production_recommendation_eligible", True),
    ],
)
def test_fully_resigned_adjudication_contract_mutation_is_rejected(
    tmp_path: Path, compiled_fixture: dict, field: str, value
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    path = root / "risk-semantic-empty-adjudication.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _write_signed(path, payload, "adjudication_canonical_sha256")
    _reject(root, _resign_fixture(root))


def test_risk_physical_raw_roles_cannot_be_content_deduplicated(
    tmp_path: Path, compiled_fixture: dict
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["calls"][16]["raw_path"] = manifest["calls"][17]["raw_path"]
    _write_signed(root / "manifest.json", manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


def test_segment_zero_rows_remain_distinct(compiled_fixture: dict) -> None:
    manifest = json.loads(
        (Path(compiled_fixture["fixture_root"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert [call["source_index"] for call in manifest["calls"] if call["items_empty"]] == [
        *SOURCE_EMPTY_INDICES,
        *RISK_EMPTY_INDICES,
    ]
    assert all(
        not manifest["calls"][index]["semantic_empty_adjudicated"]
        for index in SOURCE_EMPTY_INDICES
    )
    assert all(
        manifest["calls"][index]["semantic_empty_adjudicated"]
        for index in RISK_EMPTY_INDICES
    )


@pytest.mark.parametrize("source_index", RISK_EMPTY_INDICES)
def test_each_risk_semantic_empty_allowlist_entry_is_pinned(
    tmp_path: Path, compiled_fixture: dict, source_index: int
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    path = root / "risk-semantic-empty-adjudication.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    entry = next(row for row in payload["entries"] if row["source_index"] == source_index)
    entry["source_plan_entry_sha256"] = "0" * 64
    _write_signed(path, payload, "adjudication_canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [
        (2, "segment", RISK_SEGMENT_SCHEMA),
        (16, "segment", SOURCE_SEGMENT_SCHEMA),
        (8, "row_cap", 6000),
        (12, "row_cap", 9999),
        (12, "row_count", 7676),
        (16, "semantic_empty_adjudicated", False),
        (36, "items_empty", True),
        (14, "retrieved_at", f"{AS_OF}T00:00:00+00:00"),
    ],
)
def test_call_contract_mutations_fail_closed(
    tmp_path: Path,
    compiled_fixture: dict,
    index: int,
    field: str,
    value,
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["calls"][index][field] = value
    _write_signed(root / "manifest.json", manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("attempt", "params_json"), '{"exchange":"SZSE","list_status":"L"}'),
        (("attempt", "started_at"), "2026-07-14T05:00:00+00:00"),
        (("attempt", "http_status"), 500),
        (("attempt", "outcome"), "failed"),
        (("generation_or_receipt", "row_count"), 2314),
    ],
)
def test_fully_resigned_source_attempt_lineage_mutation_is_rejected(
    tmp_path: Path, compiled_fixture: dict, path: tuple[str, str], value
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)

    def mutate(payload: dict) -> None:
        payload[path[0]][path[1]] = value

    manifest_sha = _resign_evidence_and_repoint_call(root, 0, mutate)
    _reject(root, manifest_sha)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "complete"),
        ("http_status", 500),
        ("provider_message", "error"),
        ("raw_bytes", 1),
        ("semantic_empty", True),
        ("source_checkpoint_completion_evidence_sha256", "0" * 64),
    ],
)
def test_fully_resigned_risk_receipt_lineage_mutation_is_rejected(
    tmp_path: Path, compiled_fixture: dict, field: str, value
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)

    def mutate(payload: dict) -> None:
        payload[field] = value

    manifest_sha = _resign_evidence_and_repoint_call(root, 14, mutate)
    _reject(root, manifest_sha)


@pytest.mark.parametrize(
    ("field", "value"),
    [("http_status", 500), ("provider_code", 1), ("provider_message", "error")],
)
def test_fully_resigned_call_http_claim_mutation_is_rejected(
    tmp_path: Path, compiled_fixture: dict, field: str, value
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["calls"][0][field] = value
    _write_signed(root / "manifest.json", manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_started_at", "2026-07-14T05:57:43+00:00"),
        ("provider_http_date", "Tue, 14 Jul 2026 05:57:44 GMT"),
        ("normalized_rows_sha256", "0" * 64),
    ],
)
def test_fully_resigned_source_call_lineage_claim_is_rejected(
    tmp_path: Path, compiled_fixture: dict, field: str, value: str
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["calls"][0][field] = value
    _write_signed(manifest_path, manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


def test_fully_resigned_risk_source_raw_lineage_path_is_rejected(
    tmp_path: Path, compiled_fixture: dict
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["calls"][14]["source_physical_raw_path"] = "raw/risk/14_tampered.json"
    _write_signed(manifest_path, manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize("target", ["call", "segment"])
def test_fully_resigned_unbound_contract_field_is_rejected(
    tmp_path: Path, compiled_fixture: dict, target: str
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    container = manifest["calls"][0] if target == "call" else manifest["segments"][0]
    container["unbound_claim"] = "accepted"
    _write_signed(manifest_path, manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize("which", ["request", "response"])
def test_stk_limit_field_orders_are_independently_pinned(
    tmp_path: Path, compiled_fixture: dict, which: str
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    call = manifest["calls"][12]
    key = f"{which}_fields"
    call[key] = list(reversed(call[key]))
    call[f"{key}_sha256"] = _sha(call[key])
    _write_signed(root / "manifest.json", manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"code": 1}),
        lambda payload: payload.pop("data"),
        lambda payload: payload["data"].pop("fields"),
        lambda payload: payload["data"].pop("items"),
        lambda payload: payload.update({"msg": "error"}),
    ],
)
def test_raw_envelope_mutations_fail_closed(
    tmp_path: Path, compiled_fixture: dict, mutator
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    raw_path = root / manifest["calls"][14]["raw_path"]
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    mutator(payload)
    raw_path.write_bytes(_canonical_bytes(payload) + b"\n")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize("raw", [b"", b"{", b'{"code":NaN}'])
def test_empty_malformed_and_nonfinite_raw_fail_closed(
    tmp_path: Path, compiled_fixture: dict, raw: bytes
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    raw_path = root / manifest["calls"][14]["raw_path"]
    raw_path.write_bytes(raw)
    _reject(root, _resign_fixture(root))


def test_call_cardinality_duplicate_and_extra_fail_closed(
    tmp_path: Path, compiled_fixture: dict
) -> None:
    for mode in ("missing", "duplicate", "extra"):
        root = _copy_fixture(tmp_path / mode, compiled_fixture)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if mode == "missing":
            manifest["calls"].pop()
        elif mode == "duplicate":
            manifest["calls"][1] = dict(manifest["calls"][0])
        else:
            manifest["calls"].append(dict(manifest["calls"][-1]))
        _write_signed(root / "manifest.json", manifest, "canonical_sha256")
        _reject(root, _resign_fixture(root))


def test_fully_resigned_declared_poison_file_is_rejected(
    tmp_path: Path, compiled_fixture: dict
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    poison = root / "evidence" / "poison.json"
    poison.write_text("{}\n", encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["declared_files"].append(
        {
            "path": "evidence/poison.json",
            "bytes": poison.stat().st_size,
            "sha256": _file_sha(poison),
        }
    )
    manifest["declared_files"].sort(key=lambda row: row["path"])
    _write_signed(manifest_path, manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.json", "/absolute.json", "raw\\outside.json", "E:/outside.json"],
)
def test_external_and_noncanonical_paths_fail_closed(
    tmp_path: Path, compiled_fixture: dict, bad_path: str
) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["calls"][0]["raw_path"] = bad_path
    _write_signed(root / "manifest.json", manifest, "canonical_sha256")
    _reject(root, _resign_fixture(root))


def test_extra_sqlite_sidecar_is_rejected(tmp_path: Path, compiled_fixture: dict) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    (root / "metadata.sqlite3-wal").write_bytes(b"")
    _reject(root, compiled_fixture["manifest_file_sha256"])


def test_hardlinked_artifact_is_rejected(tmp_path: Path, compiled_fixture: dict) -> None:
    root = _copy_fixture(tmp_path, compiled_fixture)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    raw = root / manifest["calls"][0]["raw_path"]
    os.link(raw, root / "hardlink.json")
    _reject(root, compiled_fixture["manifest_file_sha256"])


@pytest.mark.parametrize(
    "evidence_use",
    ["current", "development", "embargo", "final_oos", "research-development", ""],
)
def test_non_diagnostic_uses_are_rejected(
    compiled_fixture: dict, evidence_use: str
) -> None:
    _reject(
        Path(compiled_fixture["fixture_root"]),
        compiled_fixture["manifest_file_sha256"],
        evidence_use=evidence_use,
    )


def test_invalid_run_and_audit_remain_explicitly_invalid(compiled_fixture: dict) -> None:
    root = Path(compiled_fixture["fixture_root"])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    adjudication = json.loads(
        (root / "risk-semantic-empty-adjudication.json").read_text(encoding="utf-8")
    )
    assert manifest["risk_adjudication"] == {
        **manifest["risk_adjudication"],
        "invalid_run_id": INVALID_RUN_ID,
        "invalid_run_tree_sha256": INVALID_RUN_TREE_SHA256,
        "invalid_audit_canonical_sha256": INVALID_AUDIT_CANONICAL_SHA256,
        "invalid_audit_file_sha256": INVALID_AUDIT_FILE_SHA256,
        "historical_v1_run_success_adopted": False,
    }
    assert adjudication["historical_v1_run_status"] == "invalid_fail_closed"
    assert adjudication["historical_v1_run_success_adopted"] is False
    assert adjudication["status"] == "admissible_raw_and_receipt_only"


def test_replay_has_no_network_or_credential_surface(
    monkeypatch: pytest.MonkeyPatch, compiled_fixture: dict
) -> None:
    import socket

    def explode(*args, **kwargs):
        raise AssertionError("network or write surface was reached")

    monkeypatch.setattr(socket, "create_connection", explode)
    monkeypatch.setattr(Path, "write_bytes", explode)
    monkeypatch.setattr(Path, "write_text", explode)
    monkeypatch.setattr(Path, "mkdir", explode)
    monkeypatch.setattr(os, "replace", explode)
    replayed = replay_current_pool_offline_fixture_v2_calls(
        fixture_root=compiled_fixture["fixture_root"],
        expected_manifest_sha256=compiled_fixture["manifest_file_sha256"],
        evidence_use=EVIDENCE_USE,
    )
    assert len(replayed) == 53
