"""Offline fixture successor bound to the current producer and test sources."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from app import current_pool_offline_fixture_v3 as v3
from app.durable_io import fsync_directory


FIXTURE_SCHEMA_V4 = "current-pool-offline-fixture/v4"
RECEIPT_SCHEMA_V4 = "current-pool-offline-fixture-receipt/v4"
EVIDENCE_USE = "contaminated_diagnostic_historical"
BASE_CALL_COUNT = 53
TAIL_CALL_COUNT = 6
PHYSICAL_RAW_COUNT = 56
_PRODUCER_PATHS = (
    "app/current_pool_offline_fixture_v4.py",
    "tests/test_current_pool_offline_fixture_v4.py",
    "app/current_pool_offline_fixture_v3.py",
    "tests/test_current_pool_offline_fixture_v3.py",
    "app/research_provider_pit_tail_v4.py",
    "tests/test_research_provider_pit_tail_v4.py",
)


class OfflineFixtureV4Error(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return v3._canonical(value)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict(raw: bytes, label: str) -> Any:
    try:
        return v3._strict(raw, label)
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(str(exc)) from exc


def _file(path: Path) -> bytes:
    try:
        return v3._file(path)
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(str(exc)) from exc


def _tree(root: Path) -> list[dict[str, Any]]:
    try:
        return v3._tree(root)
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(str(exc)) from exc


def _write(path: Path, raw: bytes) -> None:
    try:
        v3._write(path, raw)
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(str(exc)) from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write(path, _canonical(value) + b"\n")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def current_producer_binding_v4() -> dict[str, Any]:
    """Hash the fixed producer/test contract from actual regular files."""
    root = _project_root()
    entries: list[dict[str, Any]] = []
    for relative in _PRODUCER_PATHS:
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise OfflineFixtureV4Error("producer binding path escapes project root") from exc
        raw = _file(candidate)
        entries.append({"path": relative, "bytes": len(raw), "sha256": _sha_bytes(raw)})
    return {"schema": "current-pool-offline-fixture-producer-binding/v1", "entries": entries}


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "current-pool-offline-fixture-identity/v4",
        "content_canonical_sha256": manifest["content_canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "base": manifest["base"],
        "producer_binding_sha256": manifest["producer_binding_sha256"],
    }


def _canonical_field(payload: Mapping[str, Any], field: str, label: str) -> None:
    unsigned = dict(payload)
    claimed = unsigned.pop(field, None)
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed, _sha(unsigned)):
        raise OfflineFixtureV4Error(f"{label} canonical hash mismatch")


def _base_descriptor(root: Path) -> dict[str, Any]:
    raw = _file(root / "manifest.json")
    manifest = _strict(raw, "v3 base manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != v3.FIXTURE_SCHEMA_V3:
        raise OfflineFixtureV4Error("v3 base schema mismatch")
    return {
        "schema": v3.FIXTURE_SCHEMA_V3,
        "path": f"base/{manifest.get('fixture_id')}",
        "fixture_id": manifest.get("fixture_id"),
        "manifest_file_sha256": _sha_bytes(raw),
        "manifest_canonical_sha256": manifest.get("canonical_sha256"),
        "published_tree_sha256": _sha(_tree(root)),
        "base_calls": BASE_CALL_COUNT,
        "tail_calls": TAIL_CALL_COUNT,
        "physical_raw_files": PHYSICAL_RAW_COUNT,
    }


def _receipt_core(manifest: Mapping[str, Any], manifest_raw: bytes) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA_V4,
        "fixture_id": manifest["fixture_id"],
        "manifest_file_sha256": _sha_bytes(manifest_raw),
        "manifest_canonical_sha256": manifest["canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "producer_binding_sha256": manifest["producer_binding_sha256"],
        "base_calls": BASE_CALL_COUNT,
        "tail_calls": TAIL_CALL_COUNT,
        "physical_raw_file_count": PHYSICAL_RAW_COUNT,
        "single_writer": True,
        "atomic_publish": True,
        "network_calls": 0,
        "credential_accesses": 0,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
    }


def compile_current_pool_offline_fixture_v4(
    *,
    clean_snapshot_root: str | Path,
    invalid_run_root: str | Path,
    invalid_audit_path: str | Path,
    tail_run_root: str | Path,
    output_parent: str | Path,
) -> dict[str, Any]:
    """Publish a fresh v4 fixture without reusing a previous candidate."""
    output_parent = Path(output_parent)
    if output_parent.exists():
        raise OfflineFixtureV4Error("fixture output parent must be fresh")
    output_parent.mkdir(parents=False)
    fsync_directory(output_parent.parent)
    staging = output_parent / f".fixture-v4-staging-{os.getpid()}"
    staging.mkdir(exist_ok=False)
    base_build = staging / ".base-build"
    try:
        base_result = v3.compile_current_pool_offline_fixture_v3(
            clean_snapshot_root=clean_snapshot_root,
            invalid_run_root=invalid_run_root,
            invalid_audit_path=invalid_audit_path,
            tail_run_root=tail_run_root,
            output_parent=base_build,
        )
        base_root = Path(base_result["fixture_root"])
        v3.preflight_current_pool_offline_fixture_v3(
            fixture_root=base_root,
            expected_manifest_sha256=base_result["manifest_file_sha256"],
            evidence_use=v3.EVIDENCE_USE,
        )
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(str(exc)) from exc

    base = _base_descriptor(base_root)
    base_rel = PurePosixPath(base["path"])
    published_base = staging.joinpath(*base_rel.parts)
    published_base.parent.mkdir(exist_ok=False)
    os.rename(base_root, published_base)
    try:
        base_build.rmdir()
    except OSError as exc:
        raise OfflineFixtureV4Error("v3 base staging was not empty after atomic move") from exc
    fsync_directory(published_base.parent)
    payload_files = _tree(staging)
    binding = current_producer_binding_v4()
    manifest_core: dict[str, Any] = {
        "schema": FIXTURE_SCHEMA_V4,
        "data_cutoff": "2026-07-10",
        "temporal_role": "contaminated_diagnostic",
        "replay_mode": "historical",
        "evidence_use": EVIDENCE_USE,
        "base": base,
        "base_calls": BASE_CALL_COUNT,
        "tail_calls": TAIL_CALL_COUNT,
        "physical_raw_files": PHYSICAL_RAW_COUNT,
        "network_calls": 0,
        "credential_accesses": 0,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
        "producer_binding": binding,
        "producer_binding_sha256": _sha(binding),
        "declared_files": payload_files,
        "declared_files_root_sha256": _sha(payload_files),
    }
    manifest_core["content_canonical_sha256"] = _sha(manifest_core)
    manifest_core["fixture_id"] = _sha(_identity(manifest_core))
    manifest = dict(manifest_core)
    manifest["canonical_sha256"] = _sha(manifest)
    _write_json(staging / "manifest.json", manifest)
    manifest_raw = _file(staging / "manifest.json")
    receipt_core = _receipt_core(manifest, manifest_raw)
    receipt = dict(receipt_core)
    receipt["receipt_canonical_sha256"] = _sha(receipt_core)
    _write_json(staging / "fixture-receipt.json", receipt)
    target = output_parent / manifest["fixture_id"]
    if target.exists():
        raise OfflineFixtureV4Error("content-addressed target already exists")
    fsync_directory(staging)
    os.rename(staging, target)
    fsync_directory(output_parent)
    expected = payload_files + [
        {"path": "fixture-receipt.json", "bytes": len(_file(target / "fixture-receipt.json")), "sha256": _sha_bytes(_file(target / "fixture-receipt.json"))},
        {"path": "manifest.json", "bytes": len(_file(target / "manifest.json")), "sha256": _sha_bytes(_file(target / "manifest.json"))},
    ]
    if sorted(_tree(target), key=lambda row: row["path"]) != sorted(expected, key=lambda row: row["path"]):
        raise OfflineFixtureV4Error("published tree changed")
    return {
        "schema": FIXTURE_SCHEMA_V4,
        "fixture_root": target,
        "fixture_path": target,
        "fixture_id": manifest["fixture_id"],
        "manifest_path": target / "manifest.json",
        "manifest_file_sha256": _sha_bytes(_file(target / "manifest.json")),
        "manifest_canonical_sha256": manifest["canonical_sha256"],
        "receipt_file_sha256": _sha_bytes(_file(target / "fixture-receipt.json")),
        "receipt_canonical_sha256": receipt["receipt_canonical_sha256"],
        "producer_binding_sha256": manifest["producer_binding_sha256"],
        "base_calls": BASE_CALL_COUNT,
        "tail_calls": TAIL_CALL_COUNT,
        "physical_raw_files": PHYSICAL_RAW_COUNT,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
    }


def preflight_current_pool_offline_fixture_v4(
    *, fixture_root: str | Path, expected_manifest_sha256: str, evidence_use: str
) -> dict[str, Any]:
    try:
        if evidence_use != EVIDENCE_USE:
            raise OfflineFixtureV4Error("fixture use is outside contaminated diagnostic historical")
        root = Path(fixture_root)
        manifest_path = root / "manifest.json"
        manifest_raw = _file(manifest_path)
        if not isinstance(expected_manifest_sha256, str) or not hmac.compare_digest(_sha_bytes(manifest_raw), expected_manifest_sha256):
            raise OfflineFixtureV4Error("fixture manifest file hash mismatch")
        manifest = _strict(manifest_raw, "fixture manifest")
        if not isinstance(manifest, dict) or manifest.get("schema") != FIXTURE_SCHEMA_V4 or root.name != manifest.get("fixture_id"):
            raise OfflineFixtureV4Error("fixture schema or identity mismatch")
        _canonical_field(manifest, "canonical_sha256", "fixture manifest")
        content = {key: value for key, value in manifest.items() if key not in {"content_canonical_sha256", "fixture_id", "canonical_sha256"}}
        if manifest.get("content_canonical_sha256") != _sha(content):
            raise OfflineFixtureV4Error("fixture content identity mismatch")
        if manifest.get("fixture_id") != _sha(_identity(manifest)):
            raise OfflineFixtureV4Error("fixture id mismatch")
        expected_binding = current_producer_binding_v4()
        if manifest.get("producer_binding") != expected_binding or manifest.get("producer_binding_sha256") != _sha(expected_binding):
            raise OfflineFixtureV4Error("producer binding mismatch")
        if (
            manifest.get("data_cutoff") != "2026-07-10"
            or manifest.get("evidence_use") != EVIDENCE_USE
            or manifest.get("base_calls") != BASE_CALL_COUNT
            or manifest.get("tail_calls") != TAIL_CALL_COUNT
            or manifest.get("physical_raw_files") != PHYSICAL_RAW_COUNT
            or manifest.get("network_calls") != 0
            or manifest.get("credential_accesses") != 0
            or manifest.get("eligible_pool_count") != 0
            or manifest.get("production_recommendation_eligible") is not False
        ):
            raise OfflineFixtureV4Error("fixture safety/topology mismatch")
        payload = [row for row in _tree(root) if row["path"] not in {"manifest.json", "fixture-receipt.json"}]
        if payload != manifest.get("declared_files") or _sha(payload) != manifest.get("declared_files_root_sha256"):
            raise OfflineFixtureV4Error("fixture declared tree mismatch")
        base = manifest.get("base")
        if not isinstance(base, dict):
            raise OfflineFixtureV4Error("fixture base descriptor missing")
        base_rel = base.get("path")
        if not isinstance(base_rel, str) or not base_rel.startswith("base/") or ".." in PurePosixPath(base_rel).parts:
            raise OfflineFixtureV4Error("base path binding is unsafe")
        base_root = root.joinpath(*PurePosixPath(base_rel).parts)
        base_manifest_raw = _file(base_root / "manifest.json")
        if base.get("manifest_file_sha256") != _sha_bytes(base_manifest_raw) or base.get("published_tree_sha256") != _sha(_tree(base_root)):
            raise OfflineFixtureV4Error("base descriptor/tree binding mismatch")
        try:
            base_verified = v3.preflight_current_pool_offline_fixture_v3(
                fixture_root=base_root,
                expected_manifest_sha256=_sha_bytes(base_manifest_raw),
                evidence_use=v3.EVIDENCE_USE,
            )
        except v3.OfflineFixtureV3Error as exc:
            raise OfflineFixtureV4Error(f"v3 base rejected: {exc}") from exc
        if (
            base.get("fixture_id") != base_verified.get("fixture_id")
            or base.get("base_calls") != BASE_CALL_COUNT
            or base.get("tail_calls") != TAIL_CALL_COUNT
            or base.get("physical_raw_files") != PHYSICAL_RAW_COUNT
        ):
            raise OfflineFixtureV4Error("base topology mismatch")
        receipt = _strict(_file(root / "fixture-receipt.json"), "fixture receipt")
        if not isinstance(receipt, dict):
            raise OfflineFixtureV4Error("fixture receipt is not an object")
        _canonical_field(receipt, "receipt_canonical_sha256", "fixture receipt")
        receipt_unsigned = dict(receipt)
        receipt_unsigned.pop("receipt_canonical_sha256")
        if receipt_unsigned != _receipt_core(manifest, manifest_raw):
            raise OfflineFixtureV4Error("fixture receipt mismatch")
        return {
            "schema": FIXTURE_SCHEMA_V4,
            "fixture_root": str(root),
            "fixture_id": manifest["fixture_id"],
            "manifest_file_sha256": expected_manifest_sha256,
            "manifest_canonical_sha256": manifest["canonical_sha256"],
            "producer_binding_sha256": manifest["producer_binding_sha256"],
            "base_calls": BASE_CALL_COUNT,
            "tail_calls": TAIL_CALL_COUNT,
            "physical_raw_files": PHYSICAL_RAW_COUNT,
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
        }
    except OfflineFixtureV4Error:
        raise
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise OfflineFixtureV4Error(f"offline fixture v4 rejected: {exc}") from None


def replay_current_pool_offline_fixture_v4_calls(
    *, fixture_root: str | Path, expected_manifest_sha256: str, evidence_use: str
) -> dict[str, Any]:
    verified = preflight_current_pool_offline_fixture_v4(
        fixture_root=fixture_root,
        expected_manifest_sha256=expected_manifest_sha256,
        evidence_use=evidence_use,
    )
    manifest = _strict(_file(Path(fixture_root) / "manifest.json"), "fixture manifest")
    base_root = Path(fixture_root).joinpath(*PurePosixPath(manifest["base"]["path"]).parts)
    try:
        replayed = v3.replay_current_pool_offline_fixture_v3_calls(
            fixture_root=base_root,
            expected_manifest_sha256=_sha_bytes(_file(base_root / "manifest.json")),
            evidence_use=v3.EVIDENCE_USE,
        )
    except v3.OfflineFixtureV3Error as exc:
        raise OfflineFixtureV4Error(f"v3 replay rejected: {exc}") from exc
    return {
        "fixture_id": verified["fixture_id"],
        "base_calls": replayed["base_calls"],
        "tail_calls": TAIL_CALL_COUNT,
        "tail_evaluation_trade_date": replayed["tail_evaluation_trade_date"],
        "network_calls": 0,
        "credential_accesses": 0,
    }
