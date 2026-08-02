"""Offline provider-PIT fixture successor bound to a v4 six-call tail."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from app import current_pool_offline_fixture_v2 as v2
from app import research_provider_pit_tail_v4 as tail_v4
from app.durable_io import fsync_directory


FIXTURE_SCHEMA_V3 = "current-pool-offline-fixture/v3"
RECEIPT_SCHEMA_V3 = "current-pool-offline-fixture-receipt/v3"
EVIDENCE_USE = "contaminated_diagnostic_historical"
TAIL_SCHEMA = "provider-pit-tail-run/v4"
TAIL_CALL_COUNT = 6
BASE_CALL_COUNT = 53
PHYSICAL_RAW_COUNT = 56


class OfflineFixtureV3Error(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict(raw: bytes, label: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OfflineFixtureV3Error(f"{label} duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise OfflineFixtureV3Error(f"{label} is not strict JSON") from exc


def _file(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise OfflineFixtureV3Error(f"not a regular file: {path}")
    if int(getattr(path.stat(), "st_file_attributes", 0)) & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise OfflineFixtureV3Error(f"reparse file: {path}")
    return path.read_bytes()


def _tree(root: Path) -> list[dict[str, Any]]:
    try:
        rows = v2._tree_files(root)
    except Exception as exc:
        raise OfflineFixtureV3Error(str(exc)) from exc
    return rows


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.stat().st_nlink != 1:
        raise OfflineFixtureV3Error("published file is not independent")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write(path, _canonical(value) + b"\n")


def _canonical_field(payload: Mapping[str, Any], field: str, label: str) -> None:
    unsigned = dict(payload)
    claimed = unsigned.pop(field, None)
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed, _sha(unsigned)):
        raise OfflineFixtureV3Error(f"{label} canonical hash mismatch")


def _copy_tree(source: Path, target: Path) -> None:
    for row in _tree(source):
        relative = PurePosixPath(row["path"])
        destination = target.joinpath(*relative.parts)
        raw = _file(source.joinpath(*relative.parts))
        if len(raw) != row["bytes"] or _sha_bytes(raw) != row["sha256"]:
            raise OfflineFixtureV3Error("source file changed during copy")
        _write(destination, raw)


def _tail_plan_call(plan: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    calls = plan.get("calls") or plan.get("planned_calls")
    if not isinstance(calls, list) or len(calls) != TAIL_CALL_COUNT:
        raise OfflineFixtureV3Error("tail plan call count mismatch")
    call = calls[sequence]
    if not isinstance(call, dict):
        raise OfflineFixtureV3Error("tail call is not an object")
    return call


def _verify_tail(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    manifest_raw = _file(manifest_path)
    if len(manifest_raw) != expected.get("manifest_bytes") or _sha_bytes(manifest_raw) != expected.get("manifest_file_sha256"):
        raise OfflineFixtureV3Error("tail manifest descriptor mismatch")
    manifest = _strict(manifest_raw, "tail manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != TAIL_SCHEMA or manifest.get("status") != "complete":
        raise OfflineFixtureV3Error("tail manifest schema/status mismatch")
    _canonical_field(manifest, "manifest_canonical_sha256", "tail manifest")
    if manifest.get("manifest_canonical_sha256") != expected.get("manifest_canonical_sha256"):
        raise OfflineFixtureV3Error("tail manifest canonical mismatch")
    if manifest.get("planned_calls") != TAIL_CALL_COUNT or manifest.get("actual_calls") != TAIL_CALL_COUNT or manifest.get("formal_receipts") != TAIL_CALL_COUNT:
        raise OfflineFixtureV3Error("tail call counters mismatch")
    network = manifest.get("network")
    if not isinstance(network, dict) or network.get("allowed_connection_attempts") != TAIL_CALL_COUNT or network.get("denied_connection_attempts") != 0 or network.get("other_endpoint_calls") != 0 or network.get("retries") != 0 or network.get("cache") != 0:
        raise OfflineFixtureV3Error("tail network contract mismatch")
    plan_raw = _file(root / "plan.json")
    plan = _strict(plan_raw, "tail plan")
    if not isinstance(plan, dict):
        raise OfflineFixtureV3Error("tail plan is not an object")
    calls = plan.get("calls") or plan.get("planned_calls")
    if not isinstance(calls, list) or len(calls) != TAIL_CALL_COUNT:
        raise OfflineFixtureV3Error("tail plan calls mismatch")
    receipt_index = _strict(_file(root / "receipt-index.json"), "tail receipt index")
    if not isinstance(receipt_index, dict):
        raise OfflineFixtureV3Error("tail receipt index is not an object")
    receipt_rows = receipt_index.get("receipts") or receipt_index.get("entries")
    if not isinstance(receipt_rows, list) or len(receipt_rows) != TAIL_CALL_COUNT:
        raise OfflineFixtureV3Error("tail receipt index count mismatch")
    raw_refs: list[dict[str, Any]] = []
    receipt_refs: list[dict[str, Any]] = []
    for sequence in range(TAIL_CALL_COUNT):
        call = _tail_plan_call(plan, sequence)
        endpoint = call.get("endpoint")
        raw_candidates = sorted((root / "raw").glob(f"{sequence:02d}_{endpoint}_*.json"))
        receipt_candidates = sorted((root / "receipts").glob(f"{sequence:02d}_*.json"))
        if len(raw_candidates) != 1 or len(receipt_candidates) != 1:
            raise OfflineFixtureV3Error("tail raw/receipt path count mismatch")
        raw_path, receipt_path = raw_candidates[0], receipt_candidates[0]
        raw = _file(raw_path)
        receipt_bytes = _file(receipt_path)
        receipt = _strict(receipt_bytes, f"tail receipt {sequence}")
        if not isinstance(receipt, dict):
            raise OfflineFixtureV3Error("tail receipt is not an object")
        _canonical_field(receipt, "receipt_canonical_sha256", f"tail receipt {sequence}")
        result = tail_v4.validate_provider_envelope_v4(call, raw)
        receipt_rows = receipt.get("rows")
        receipt_raw_section = receipt.get("raw")
        if not isinstance(receipt_rows, dict) or not isinstance(receipt_raw_section, dict):
            raise OfflineFixtureV3Error("tail receipt rows/raw sections missing")
        if result.get("row_count") != receipt_rows.get("count") or receipt_raw_section.get("sha256") != _sha_bytes(raw):
            raise OfflineFixtureV3Error("tail receipt/raw normalization mismatch")
        if receipt.get("sequence") != sequence or receipt_raw_section.get("sha256") != _sha_bytes(raw):
            raise OfflineFixtureV3Error("tail receipt identity mismatch")
        if sequence == 5 and (not result.get("semantic_empty") or result.get("semantic_empty_reason") != "no_events_in_partition"):
            raise OfflineFixtureV3Error("tail semantic-empty evidence missing")
        raw_refs.append({"sequence": sequence, "path": f"raw/{raw_path.name}", "bytes": len(raw), "sha256": _sha_bytes(raw), "row_count": result["row_count"]})
        receipt_refs.append({"sequence": sequence, "path": f"receipts/{receipt_path.name}", "bytes": len(receipt_bytes), "sha256": _sha_bytes(receipt_bytes), "canonical_sha256": receipt["receipt_canonical_sha256"]})
    return {"manifest": manifest, "raw_refs": raw_refs, "receipt_refs": receipt_refs, "evaluation_trade_date": manifest.get("data_cutoff")}


def _identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "current-pool-offline-fixture-identity/v3",
        "content_canonical_sha256": manifest["content_canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "base": manifest["base"],
        "tail": manifest["tail"],
    }


def compile_current_pool_offline_fixture_v3(*, clean_snapshot_root: str | Path, invalid_run_root: str | Path, invalid_audit_path: str | Path, tail_run_root: str | Path, output_parent: str | Path) -> dict[str, Any]:
    output_parent = Path(output_parent)
    if output_parent.exists():
        raise OfflineFixtureV3Error("fixture output parent must be fresh")
    output_parent.mkdir(parents=False)
    fsync_directory(output_parent.parent)
    base_build = output_parent.parent / f".offline-fixture-v3-base-{os.getpid()}"
    if base_build.exists():
        raise OfflineFixtureV3Error("base build staging already exists")
    base_result = v2.compile_current_pool_offline_fixture_v2(clean_snapshot_root=clean_snapshot_root, invalid_run_root=invalid_run_root, invalid_audit_path=invalid_audit_path, output_parent=base_build)
    base_root = Path(base_result["fixture_root"])
    v2.preflight_current_pool_offline_fixture_v2(fixture_root=base_root, expected_manifest_sha256=base_result["manifest_file_sha256"], evidence_use=v2.EVIDENCE_USE)
    tail_root = Path(tail_run_root)
    tail_manifest = _strict(_file(tail_root / "manifest.json"), "tail manifest")
    if not isinstance(tail_manifest, dict):
        raise OfflineFixtureV3Error("tail manifest is not an object")
    tail_binding = {"manifest_file_sha256": _sha_bytes(_file(tail_root / "manifest.json")), "manifest_bytes": len(_file(tail_root / "manifest.json")), "manifest_canonical_sha256": tail_manifest.get("manifest_canonical_sha256"), "run_id": tail_manifest.get("run_id")}
    tail_info = _verify_tail(tail_root, tail_binding)
    staging = output_parent / f".fixture-v3-staging-{os.getpid()}"
    staging.mkdir(exist_ok=False)
    base_rel = Path("base") / base_result["fixture_id"]
    _copy_tree(base_root, staging / base_rel)
    _copy_tree(tail_root, staging / "tail")
    payload_files = _tree(staging)
    base_manifest = _strict(_file(staging / base_rel / "manifest.json"), "base manifest")
    if not isinstance(base_manifest, dict):
        raise OfflineFixtureV3Error("base manifest is not an object")
    base_descriptor = {"schema": v2.FIXTURE_SCHEMA_V2, "path": base_rel.as_posix(), "fixture_id": base_manifest.get("fixture_id"), "manifest_file_sha256": _sha_bytes(_file(staging / base_rel / "manifest.json")), "manifest_canonical_sha256": base_manifest.get("canonical_sha256"), "published_tree_sha256": _sha(_tree(staging / base_rel)), "call_count": BASE_CALL_COUNT}
    tail_descriptor = {"schema": TAIL_SCHEMA, "run_id": tail_manifest.get("run_id"), "manifest_file_sha256": tail_binding["manifest_file_sha256"], "manifest_canonical_sha256": tail_binding["manifest_canonical_sha256"], "receipt_index_file_sha256": _sha_bytes(_file(staging / "tail" / "receipt-index.json")), "raw_refs": tail_info["raw_refs"], "receipt_refs": tail_info["receipt_refs"], "call_count": TAIL_CALL_COUNT, "evaluation_trade_date": tail_info["evaluation_trade_date"]}
    manifest_core = {"schema": FIXTURE_SCHEMA_V3, "data_cutoff": "2026-07-10", "temporal_role": "contaminated_diagnostic", "replay_mode": "historical", "evidence_use": EVIDENCE_USE, "base": base_descriptor, "tail": tail_descriptor, "base_calls": BASE_CALL_COUNT, "tail_calls": TAIL_CALL_COUNT, "physical_raw_files": PHYSICAL_RAW_COUNT, "network_calls": 0, "credential_accesses": 0, "eligible_pool_count": 0, "production_recommendation_eligible": False, "declared_files": payload_files, "declared_files_root_sha256": _sha(payload_files)}
    manifest_core["content_canonical_sha256"] = _sha(manifest_core)
    manifest_core["fixture_id"] = _sha(_identity(manifest_core))
    manifest = dict(manifest_core)
    manifest["canonical_sha256"] = _sha(manifest)
    _write_json(staging / "manifest.json", manifest)
    receipt_core = {"schema": RECEIPT_SCHEMA_V3, "fixture_id": manifest["fixture_id"], "manifest_file_sha256": _sha_bytes(_file(staging / "manifest.json")), "manifest_canonical_sha256": manifest["canonical_sha256"], "declared_files_root_sha256": manifest["declared_files_root_sha256"], "base_calls": BASE_CALL_COUNT, "tail_calls": TAIL_CALL_COUNT, "physical_raw_file_count": PHYSICAL_RAW_COUNT, "single_writer": True, "atomic_publish": True, "network_calls": 0, "credential_accesses": 0, "eligible_pool_count": 0, "production_recommendation_eligible": False}
    receipt = dict(receipt_core)
    receipt["receipt_canonical_sha256"] = _sha(receipt_core)
    _write_json(staging / "fixture-receipt.json", receipt)
    target = output_parent / manifest["fixture_id"]
    if target.exists():
        raise OfflineFixtureV3Error("content-addressed target already exists")
    fsync_directory(staging)
    os.rename(staging, target)
    fsync_directory(output_parent)
    published = _tree(target)
    expected_published = payload_files + [{"path": "fixture-receipt.json", "bytes": len(_file(target / "fixture-receipt.json")), "sha256": _sha_bytes(_file(target / "fixture-receipt.json"))}, {"path": "manifest.json", "bytes": len(_file(target / "manifest.json")), "sha256": _sha_bytes(_file(target / "manifest.json"))}]
    if sorted(published, key=lambda row: row["path"]) != sorted(expected_published, key=lambda row: row["path"]):
        raise OfflineFixtureV3Error("published tree changed")
    return {"schema": FIXTURE_SCHEMA_V3, "fixture_root": target, "fixture_path": target, "fixture_id": manifest["fixture_id"], "manifest_path": target / "manifest.json", "manifest_file_sha256": _sha_bytes(_file(target / "manifest.json")), "manifest_canonical_sha256": manifest["canonical_sha256"], "receipt_file_sha256": _sha_bytes(_file(target / "fixture-receipt.json")), "receipt_canonical_sha256": receipt["receipt_canonical_sha256"], "physical_raw_files": PHYSICAL_RAW_COUNT, "base_calls": BASE_CALL_COUNT, "tail_calls": TAIL_CALL_COUNT, "eligible_pool_count": 0, "production_recommendation_eligible": False}


def preflight_current_pool_offline_fixture_v3(*, fixture_root: str | Path, expected_manifest_sha256: str, evidence_use: str) -> dict[str, Any]:
    try:
        if evidence_use != EVIDENCE_USE:
            raise OfflineFixtureV3Error("fixture use is outside contaminated diagnostic historical")
        root = Path(fixture_root)
        manifest_path = root / "manifest.json"
        raw = _file(manifest_path)
        if not isinstance(expected_manifest_sha256, str) or not hmac.compare_digest(_sha_bytes(raw), expected_manifest_sha256):
            raise OfflineFixtureV3Error("fixture manifest file hash mismatch")
        manifest = _strict(raw, "fixture manifest")
        if not isinstance(manifest, dict) or manifest.get("schema") != FIXTURE_SCHEMA_V3 or root.name != manifest.get("fixture_id"):
            raise OfflineFixtureV3Error("fixture schema or identity mismatch")
        _canonical_field(manifest, "canonical_sha256", "fixture manifest")
        core = dict(manifest)
        core.pop("canonical_sha256", None)
        if manifest.get("content_canonical_sha256") != _sha({k: v for k, v in core.items() if k not in {"content_canonical_sha256", "fixture_id"}}):
            raise OfflineFixtureV3Error("fixture content identity mismatch")
        identity = _identity(manifest)
        if manifest.get("fixture_id") != _sha(identity):
            raise OfflineFixtureV3Error("fixture id mismatch")
        if manifest.get("data_cutoff") != "2026-07-10" or manifest.get("evidence_use") != EVIDENCE_USE or manifest.get("base_calls") != BASE_CALL_COUNT or manifest.get("tail_calls") != TAIL_CALL_COUNT or manifest.get("physical_raw_files") != PHYSICAL_RAW_COUNT or manifest.get("eligible_pool_count") != 0 or manifest.get("production_recommendation_eligible") is not False:
            raise OfflineFixtureV3Error("fixture safety/topology mismatch")
        payload = [row for row in _tree(root) if row["path"] not in {"manifest.json", "fixture-receipt.json"}]
        if payload != manifest.get("declared_files") or _sha(payload) != manifest.get("declared_files_root_sha256"):
            raise OfflineFixtureV3Error("fixture declared tree mismatch")
        receipt = _strict(_file(root / "fixture-receipt.json"), "fixture receipt")
        if not isinstance(receipt, dict):
            raise OfflineFixtureV3Error("fixture receipt is not an object")
        _canonical_field(receipt, "receipt_canonical_sha256", "fixture receipt")
        receipt_unsigned = dict(receipt)
        receipt_unsigned.pop("receipt_canonical_sha256")
        expected_receipt = {"schema": RECEIPT_SCHEMA_V3, "fixture_id": manifest["fixture_id"], "manifest_file_sha256": _sha_bytes(raw), "manifest_canonical_sha256": manifest["canonical_sha256"], "declared_files_root_sha256": manifest["declared_files_root_sha256"], "base_calls": BASE_CALL_COUNT, "tail_calls": TAIL_CALL_COUNT, "physical_raw_file_count": PHYSICAL_RAW_COUNT, "single_writer": True, "atomic_publish": True, "network_calls": 0, "credential_accesses": 0, "eligible_pool_count": 0, "production_recommendation_eligible": False}
        if receipt_unsigned != expected_receipt:
            raise OfflineFixtureV3Error("fixture receipt mismatch")
        base_rel = manifest["base"].get("path")
        if not isinstance(base_rel, str) or not base_rel.startswith("base/") or ".." in PurePosixPath(base_rel).parts:
            raise OfflineFixtureV3Error("base path binding is unsafe")
        base_root = root.joinpath(*PurePosixPath(base_rel).parts)
        base_manifest = _strict(_file(base_root / "manifest.json"), "base manifest")
        if not isinstance(base_manifest, dict):
            raise OfflineFixtureV3Error("base manifest is not an object")
        base_manifest_sha = _sha_bytes(_file(base_root / "manifest.json"))
        if base_manifest_sha != manifest["base"].get("manifest_file_sha256"):
            raise OfflineFixtureV3Error("base manifest binding mismatch")
        base_verified = v2.preflight_current_pool_offline_fixture_v2(fixture_root=base_root, expected_manifest_sha256=base_manifest_sha, evidence_use=v2.EVIDENCE_USE)
        if base_verified.get("call_count") != BASE_CALL_COUNT:
            raise OfflineFixtureV3Error("base call count mismatch")
        if manifest["base"].get("fixture_id") != base_verified.get("fixture_id") or manifest["base"].get("published_tree_sha256") != _sha(_tree(base_root)):
            raise OfflineFixtureV3Error("base descriptor/tree binding mismatch")
        adjudication = _strict(_file(base_root / "risk-semantic-empty-adjudication.json"), "adjudication")
        entries = adjudication.get("entries") if isinstance(adjudication, dict) else None
        if not isinstance(entries, list) or len(entries) != 20 or [entry.get("source_index") for entry in entries] != list(range(16, 36)):
            raise OfflineFixtureV3Error("risk semantic-empty allowlist is not unique/exact")
        tail_verified = _verify_tail(root / "tail", manifest["tail"] | {"manifest_bytes": manifest["tail"].get("manifest_bytes", _file(root / "tail" / "manifest.json").__len__()), "manifest_file_sha256": manifest["tail"]["manifest_file_sha256"]})
        if tail_verified["manifest"].get("run_id") != manifest["tail"].get("run_id"):
            raise OfflineFixtureV3Error("tail run binding mismatch")
        if tail_verified["raw_refs"] != manifest["tail"].get("raw_refs") or tail_verified["receipt_refs"] != manifest["tail"].get("receipt_refs"):
            raise OfflineFixtureV3Error("tail raw/receipt descriptor binding mismatch")
        raw_count = len([row for row in payload if row["path"].startswith(f"{base_rel}/raw/") or row["path"].startswith("tail/raw/")])
        if raw_count != PHYSICAL_RAW_COUNT:
            raise OfflineFixtureV3Error("physical raw count mismatch")
        return {"schema": FIXTURE_SCHEMA_V3, "fixture_root": str(root), "manifest_path": str(manifest_path), "fixture_id": manifest["fixture_id"], "manifest_file_sha256": expected_manifest_sha256, "manifest_canonical_sha256": manifest["canonical_sha256"], "base_calls": BASE_CALL_COUNT, "tail_calls": TAIL_CALL_COUNT, "physical_raw_files": PHYSICAL_RAW_COUNT, "tail_evaluation_trade_date": tail_verified["evaluation_trade_date"], "eligible_pool_count": 0, "production_recommendation_eligible": False}
    except OfflineFixtureV3Error:
        raise
    except (OSError, TypeError, ValueError, KeyError, sqlite3.Error) as exc:
        raise OfflineFixtureV3Error(f"offline fixture v3 rejected: {exc}") from None


def replay_current_pool_offline_fixture_v3_calls(*, fixture_root: str | Path, expected_manifest_sha256: str, evidence_use: str) -> dict[str, Any]:
    verified = preflight_current_pool_offline_fixture_v3(fixture_root=fixture_root, expected_manifest_sha256=expected_manifest_sha256, evidence_use=evidence_use)
    manifest = _strict(_file(Path(fixture_root) / "manifest.json"), "fixture manifest")
    base_root = Path(fixture_root).joinpath(*PurePosixPath(manifest["base"]["path"]).parts)
    base_calls = v2.replay_current_pool_offline_fixture_v2_calls(fixture_root=base_root, expected_manifest_sha256=_sha_bytes(_file(base_root / "manifest.json")), evidence_use=v2.EVIDENCE_USE)
    return {"base_calls": base_calls, "tail_calls": verified["tail_calls"], "tail_evaluation_trade_date": verified["tail_evaluation_trade_date"], "fixture_id": verified["fixture_id"], "network_calls": 0}
