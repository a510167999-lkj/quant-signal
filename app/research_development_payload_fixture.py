"""Content-addressed, read-only fixture roots for development PIT payloads."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.durable_io import fsync_directory, fsync_file


class DevelopmentPayloadFixtureError(ValueError):
    pass


_DESCRIPTOR_SCHEMA = "research-composite-universe-descriptor/v1"
_PAYLOAD_SCHEMA = "research-development-payload-descriptor/v2"
_FIXTURE_SCHEMA = "research-development-payload-fixture/v2"
_RECEIPT_SCHEMA = "research-development-payload-fixture-receipt/v2"
_FILE_NAMES = frozenset({"payload-descriptor.json", "manifest.json", "receipt.json"})
_DATE_SEGMENTS = (
    ("2022-01-04", "2022-12-30"),
    ("2023-01-03", "2023-12-29"),
)
_BOUNDARY_GAPS = ({"previous_end_date": "2022-12-30", "next_start_date": "2023-01-03"},)
_HEX = frozenset("0123456789abcdef")
_PROTECTED_PATH_PARTS = frozenset(
    {
        "source_mirror",
        "source_mirrors",
        "provider_pit_tail_runs",
        "auto-iter-036-h1-hold3-development",
        "auto-iter-037-h1-hold3-development",
        "current_pool_risk_evidence_runs",
        "current_pool_runs",
    }
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture_producer_source_sha256() -> str:
    return _sha_bytes(_read_regular(Path(__file__), "fixture producer source"))


def _require_sha(value: Any, label: str) -> str:
    if type(value) is not str or len(value) != 64 or set(value) - _HEX:
        raise DevelopmentPayloadFixtureError(f"{label} is not a lowercase SHA-256")
    return value


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise DevelopmentPayloadFixtureError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise DevelopmentPayloadFixtureError(f"{label} is not an object")
    return value


def _require_canonical_file(raw: bytes, value: Mapping[str, Any], label: str) -> None:
    if raw != _canonical_bytes(value) + b"\n":
        raise DevelopmentPayloadFixtureError(f"{label} is not canonical JSON bytes")


def _safe_regular(path: Path, label: str) -> Path:
    _assert_plain_ancestors(path, label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is missing") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise DevelopmentPayloadFixtureError(f"{label} is not a plain regular file")
    return path.resolve(strict=True)


def _safe_directory(path: Path, label: str) -> Path:
    _assert_plain_ancestors(path, label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is missing") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise DevelopmentPayloadFixtureError(f"{label} is not a plain directory")
    return path.resolve(strict=True)


def _assert_plain_ancestors(path: Path, label: str) -> None:
    current = path.parent
    while current != current.parent:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DevelopmentPayloadFixtureError(f"{label} ancestor is missing") from exc
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if current.is_symlink() or attributes & int(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise DevelopmentPayloadFixtureError(f"{label} ancestor is a symlink or reparse point")
        current = current.parent


def _reject_protected_lineage_path(path: Path, label: str) -> None:
    parts = [part.lower() for part in path.parts]
    if (
        any(part in _PROTECTED_PATH_PARTS for part in parts)
        or any(part.startswith("current_pool_") for part in parts)
        or any(part.startswith("current-pool-offline") for part in parts)
        or any(part.startswith("provider_pit_tail") for part in parts)
    ):
        raise DevelopmentPayloadFixtureError(f"{label} points to protected non-development lineage")


def _require_within_workspace(path: Path, workspace_root: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is outside the caller trusted workspace") from exc
    return resolved


def _require_declared_within_workspace(path: Path, workspace_root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is outside the caller trusted workspace") from exc
    return resolved


def _read_regular(path: Path, label: str) -> bytes:
    return _safe_regular(path, label).read_bytes()


def _source_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, "source manifest")
    payload = _strict_json(raw, "source manifest")
    expected = _require_sha(payload.get("manifest_sha256"), "source manifest canonical SHA")
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    if not hmac.compare_digest(expected, _canonical_sha(unsigned)):
        raise DevelopmentPayloadFixtureError("source manifest canonical SHA mismatch")
    return payload, raw


def _source_segment_from_descriptor(
    descriptor: Mapping[str, Any],
    *,
    sequence: int,
    temporal_contract_sha256: str,
    workspace_root: Path,
) -> dict[str, Any]:
    expected_keys = {
        "path",
        "expected_artifact_root_sha256",
        "expected_coverage_audit_sha256",
        "expected_temporal_contract_sha256",
        "expected_artifact_manifest_sha256",
        "expected_temporal_role",
    }
    if set(descriptor) != expected_keys:
        raise DevelopmentPayloadFixtureError("source descriptor segment fields are invalid")
    metadata_path = _safe_regular(Path(str(descriptor["path"])), "source metadata")
    _require_within_workspace(metadata_path, workspace_root, "source metadata")
    _reject_protected_lineage_path(metadata_path, "source descriptor segment")
    if metadata_path.name != "metadata.sqlite3":
        raise DevelopmentPayloadFixtureError("source metadata filename is invalid")
    manifest_path = _safe_regular(metadata_path.parent / "manifest.json", "source manifest")
    _require_within_workspace(manifest_path, workspace_root, "source manifest")
    manifest, manifest_raw = _source_manifest(manifest_path)
    coverage = manifest.get("coverage")
    binding = manifest.get("temporal_binding")
    market = manifest.get("market_generations")
    stock = manifest.get("stock_generation")
    if not all(isinstance(value, Mapping) for value in (coverage, binding, market, stock)):
        raise DevelopmentPayloadFixtureError("source manifest lineage is incomplete")
    expected_coverage = {"start_date": _DATE_SEGMENTS[sequence - 1][0], "end_date": _DATE_SEGMENTS[sequence - 1][1]}
    if (
        manifest.get("schema_version") != "audited-pit-universe/v5"
        or manifest.get("artifact_role") != "development_only"
        or manifest.get("final_oos_eligible") is not False
        or dict(coverage) != expected_coverage
        or binding.get("role") != "development"
        or binding.get("promotion_eligible") is not False
        or binding.get("start_date") != expected_coverage["start_date"]
        or binding.get("end_date") != expected_coverage["end_date"]
        or binding.get("contract_sha256") != temporal_contract_sha256
        or descriptor["expected_temporal_contract_sha256"] != temporal_contract_sha256
        or descriptor["expected_temporal_role"] != "development"
        or descriptor["expected_artifact_root_sha256"] != manifest.get("artifact_root_sha256")
        or descriptor["expected_coverage_audit_sha256"] != manifest.get("coverage_audit_sha256")
        or descriptor["expected_artifact_manifest_sha256"] != manifest.get("manifest_sha256")
    ):
        raise DevelopmentPayloadFixtureError("source segment coverage, temporal role, or external anchor mismatch")
    for value, label in (
        (manifest.get("artifact_root_sha256"), "source artifact root"),
        (manifest.get("coverage_audit_sha256"), "source coverage root"),
        (manifest.get("manifest_sha256"), "source manifest root"),
        (market.get("root_sha256"), "source market root"),
        (stock.get("lineage_sha256"), "source stock lineage"),
        (manifest.get("producer_code_sha256"), "source producer hash"),
    ):
        _require_sha(value, label)
    metadata_raw = _read_regular(metadata_path, "source metadata")
    return {
        "sequence": sequence,
        "coverage": expected_coverage,
        "metadata_path": str(metadata_path),
        "metadata_bytes": len(metadata_raw),
        "metadata_sha256": _sha_bytes(metadata_raw),
        "manifest_path": str(manifest_path),
        "manifest_bytes": len(manifest_raw),
        "manifest_file_sha256": _sha_bytes(manifest_raw),
        "artifact_root_sha256": manifest["artifact_root_sha256"],
        "coverage_audit_sha256": manifest["coverage_audit_sha256"],
        "artifact_manifest_sha256": manifest["manifest_sha256"],
        "market_generation_root_sha256": market["root_sha256"],
        "stock_generation_lineage_sha256": stock["lineage_sha256"],
        "producer_code_sha256": manifest["producer_code_sha256"],
        "temporal_contract_sha256": temporal_contract_sha256,
        "temporal_role": "development",
    }


def _composite_root(segments: list[dict[str, Any]], contract: str) -> str:
    authority = {
        "schema_version": "research-composite-universe/v1",
        "coverage": {"start_date": _DATE_SEGMENTS[0][0], "end_date": _DATE_SEGMENTS[-1][1]},
        "temporal_role": "development",
        "temporal_contract_sha256": contract,
        "permitted_boundary_gaps": list(_BOUNDARY_GAPS),
        "segments": [
            {
                "sequence": segment["sequence"],
                "coverage": segment["coverage"],
                "artifact_root_sha256": segment["artifact_root_sha256"],
                "coverage_audit_sha256": segment["coverage_audit_sha256"],
                "temporal_contract_sha256": contract,
                "temporal_role": "development",
                "artifact_manifest_sha256": segment["artifact_manifest_sha256"],
                "market_generation_root_sha256": segment["market_generation_root_sha256"],
                "stock_generation_lineage_sha256": segment["stock_generation_lineage_sha256"],
            }
            for segment in segments
        ],
    }
    return _canonical_sha(authority)


def _read_source_descriptor(
    source_descriptor_path: str | Path,
    *,
    expected_composite_root_sha256: str,
    expected_temporal_contract_sha256: str,
    workspace_root: Path,
) -> tuple[dict[str, Any], bytes, list[dict[str, Any]]]:
    expected_root = _require_sha(expected_composite_root_sha256, "expected composite root")
    contract = _require_sha(expected_temporal_contract_sha256, "expected temporal contract")
    path = _safe_regular(Path(source_descriptor_path), "source descriptor")
    _require_within_workspace(path, workspace_root, "source descriptor")
    _reject_protected_lineage_path(path, "source descriptor")
    raw = _read_regular(path, "source descriptor")
    payload = _strict_json(raw, "source descriptor")
    _require_canonical_file(raw, payload, "source descriptor")
    if set(payload) != {"schema_version", "segments", "permitted_boundary_gaps"}:
        raise DevelopmentPayloadFixtureError("source descriptor fields are invalid")
    if payload.get("schema_version") != _DESCRIPTOR_SCHEMA or payload.get("permitted_boundary_gaps") != list(_BOUNDARY_GAPS):
        raise DevelopmentPayloadFixtureError("source descriptor schema or boundary is invalid")
    descriptors = payload.get("segments")
    if not isinstance(descriptors, list) or len(descriptors) != 2:
        raise DevelopmentPayloadFixtureError("source descriptor must contain exactly two segments")
    segments = [
        _source_segment_from_descriptor(
            item,
            sequence=index,
            temporal_contract_sha256=contract,
            workspace_root=workspace_root,
        )
        for index, item in enumerate(descriptors, 1)
        if isinstance(item, Mapping)
    ]
    if len(segments) != 2:
        raise DevelopmentPayloadFixtureError("source descriptor segments are invalid")
    if not hmac.compare_digest(_composite_root(segments, contract), expected_root):
        raise DevelopmentPayloadFixtureError("source descriptor composite root mismatch")
    return payload, raw, segments


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_file(path)


def _payload_descriptor(
    *,
    source_descriptor_raw: bytes,
    segments: list[dict[str, Any]],
    composite_root_sha256: str,
    temporal_contract_sha256: str,
    producer_source_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": _PAYLOAD_SCHEMA,
        "temporal_role": "development",
        "date_bounds": {"start_date": _DATE_SEGMENTS[0][0], "end_date": _DATE_SEGMENTS[-1][1]},
        "composite_root_sha256": composite_root_sha256,
        "temporal_contract_sha256": temporal_contract_sha256,
        "producer_source_sha256": producer_source_sha256,
        "source_descriptor": {
            "bytes": len(source_descriptor_raw),
            "file_sha256": _sha_bytes(source_descriptor_raw),
        },
        "source_segments": segments,
        "source_segments_root_sha256": _canonical_sha(segments),
    }


def _read_fixture_files(root: Path) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes]:
    safe_root = _safe_directory(root, "development payload fixture root")
    entries = {item.name for item in safe_root.iterdir()}
    if entries != _FILE_NAMES:
        raise DevelopmentPayloadFixtureError("development payload fixture tree is invalid")
    descriptor_raw = _read_regular(safe_root / "payload-descriptor.json", "payload descriptor")
    manifest_raw = _read_regular(safe_root / "manifest.json", "fixture manifest")
    receipt_raw = _read_regular(safe_root / "receipt.json", "fixture receipt")
    descriptor = _strict_json(descriptor_raw, "payload descriptor")
    manifest = _strict_json(manifest_raw, "fixture manifest")
    receipt = _strict_json(receipt_raw, "fixture receipt")
    _require_canonical_file(descriptor_raw, descriptor, "payload descriptor")
    _require_canonical_file(manifest_raw, manifest, "fixture manifest")
    _require_canonical_file(receipt_raw, receipt, "fixture receipt")
    return (
        descriptor, descriptor_raw,
        manifest, manifest_raw,
        receipt, receipt_raw,
    )


def _verify_segment_payload(
    value: Any,
    sequence: int,
    contract: str,
    workspace_root: Path,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentPayloadFixtureError("fixture source segment is invalid")
    segment = dict(value)
    expected_keys = {
        "sequence",
        "coverage",
        "metadata_path",
        "metadata_bytes",
        "metadata_sha256",
        "manifest_path",
        "manifest_bytes",
        "manifest_file_sha256",
        "artifact_root_sha256",
        "coverage_audit_sha256",
        "artifact_manifest_sha256",
        "market_generation_root_sha256",
        "stock_generation_lineage_sha256",
        "producer_code_sha256",
        "temporal_contract_sha256",
        "temporal_role",
    }
    if set(segment) != expected_keys:
        raise DevelopmentPayloadFixtureError("fixture source segment fields are invalid")
    expected = _DATE_SEGMENTS[sequence - 1]
    if (
        segment.get("sequence") != sequence
        or segment.get("coverage") != {"start_date": expected[0], "end_date": expected[1]}
        or segment.get("temporal_contract_sha256") != contract
        or segment.get("temporal_role") != "development"
    ):
        raise DevelopmentPayloadFixtureError("fixture source segment coverage is invalid")
    metadata_path = _safe_regular(Path(str(segment.get("metadata_path") or "")), "fixture source metadata")
    manifest_path = _safe_regular(Path(str(segment.get("manifest_path") or "")), "fixture source manifest")
    _require_within_workspace(metadata_path, workspace_root, "fixture source metadata")
    _require_within_workspace(manifest_path, workspace_root, "fixture source manifest")
    _reject_protected_lineage_path(metadata_path, "fixture source metadata")
    _reject_protected_lineage_path(manifest_path, "fixture source manifest")
    if manifest_path != metadata_path.parent / "manifest.json":
        raise DevelopmentPayloadFixtureError("fixture source manifest path is not adjacent to metadata")
    metadata_raw = _read_regular(metadata_path, "fixture source metadata")
    manifest, manifest_raw = _source_manifest(manifest_path)
    coverage = manifest.get("coverage")
    binding = manifest.get("temporal_binding")
    if (
        manifest.get("schema_version") != "audited-pit-universe/v5"
        or manifest.get("artifact_role") != "development_only"
        or manifest.get("final_oos_eligible") is not False
        or coverage != segment["coverage"]
        or not isinstance(binding, Mapping)
        or binding.get("role") != "development"
        or binding.get("promotion_eligible") is not False
        or binding.get("contract_sha256") != contract
        or binding.get("start_date") != expected[0]
        or binding.get("end_date") != expected[1]
    ):
        raise DevelopmentPayloadFixtureError("fixture source manifest temporal evidence is invalid")
    checks = (
        (len(metadata_raw), segment.get("metadata_bytes")),
        (_sha_bytes(metadata_raw), segment.get("metadata_sha256")),
        (len(manifest_raw), segment.get("manifest_bytes")),
        (_sha_bytes(manifest_raw), segment.get("manifest_file_sha256")),
        (manifest.get("artifact_root_sha256"), segment.get("artifact_root_sha256")),
        (manifest.get("coverage_audit_sha256"), segment.get("coverage_audit_sha256")),
        (manifest.get("manifest_sha256"), segment.get("artifact_manifest_sha256")),
        ((manifest.get("market_generations") or {}).get("root_sha256"), segment.get("market_generation_root_sha256")),
        ((manifest.get("stock_generation") or {}).get("lineage_sha256"), segment.get("stock_generation_lineage_sha256")),
        (manifest.get("producer_code_sha256"), segment.get("producer_code_sha256")),
    )
    if any(left != right for left, right in checks):
        raise DevelopmentPayloadFixtureError("fixture source metadata or lineage mismatch")
    return segment


def _required_source_anchors(value: Any, workspace_root: Path) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise DevelopmentPayloadFixtureError("expected source anchor set is invalid")
    anchors: list[dict[str, Any]] = []
    for sequence, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise DevelopmentPayloadFixtureError("expected source anchor is invalid")
        anchor = dict(item)
        _verify_segment_payload(
            anchor,
            sequence,
            str(anchor.get("temporal_contract_sha256") or ""),
            workspace_root,
        )
        anchors.append(anchor)
    return anchors


def _require_iso_date(value: Any, label: str) -> str:
    if type(value) is not str or len(value) != 10:
        raise DevelopmentPayloadFixtureError(f"{label} is invalid")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise DevelopmentPayloadFixtureError(f"{label} is invalid") from exc
    return value


class DevelopmentPayloadLoader:
    """Read the verified development payload through a fixed, bounded SQLite query."""

    def __init__(self, segments: Sequence[Mapping[str, Any]], workspace_root: Path) -> None:
        self._segments = tuple(dict(segment) for segment in segments)
        self._workspace_root = workspace_root

    def _verified_metadata(self, segment: Mapping[str, Any], label: str) -> Path:
        metadata_path = _safe_regular(Path(segment["metadata_path"]), label)
        _require_within_workspace(metadata_path, self._workspace_root, label)
        _reject_protected_lineage_path(metadata_path, label)
        raw = _read_regular(metadata_path, label)
        if len(raw) != segment["metadata_bytes"] or _sha_bytes(raw) != segment["metadata_sha256"]:
            raise DevelopmentPayloadFixtureError("loader source metadata differs from the verified anchor")
        return metadata_path

    def read_trade_sessions(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        start = _require_iso_date(start_date, "loader start date")
        end = _require_iso_date(end_date, "loader end date")
        if start > end:
            raise DevelopmentPayloadFixtureError("loader date range is invalid")
        candidates = [
            segment
            for segment in self._segments
            if segment["coverage"]["start_date"] <= start
            and end <= segment["coverage"]["end_date"]
        ]
        if len(candidates) != 1:
            raise DevelopmentPayloadFixtureError("loader request crosses a prohibited temporal boundary")
        metadata_path = self._verified_metadata(candidates[0], "loader source metadata")
        uri = f"{metadata_path.as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                "SELECT exchange, cal_date, is_open, pretrade_date, receipt_dataset, receipt_partition "
                "FROM trade_sessions WHERE cal_date >= ? AND cal_date <= ? "
                "ORDER BY cal_date, exchange",
                (start, end),
            )
            rows = [dict(row) for row in cursor]
            cursor.close()
        except sqlite3.Error as exc:
            raise DevelopmentPayloadFixtureError("loader read failed") from exc
        finally:
            connection.close()
        self._verified_metadata(candidates[0], "loader source metadata")
        for row in rows:
            date = row.get("cal_date")
            if type(date) is not str or not (start <= date <= end):
                raise DevelopmentPayloadFixtureError("loader returned an out-of-range row")
        return rows


def verify_development_payload_fixture(
    fixture_root: str | Path,
    *,
    expected_fixture_id: str,
    expected_manifest_file_sha256: str,
    expected_receipt_file_sha256: str,
    expected_composite_root_sha256: str,
    expected_temporal_contract_sha256: str,
    expected_producer_source_sha256: str,
    expected_source_descriptor_file_sha256: str,
    expected_source_anchors: Sequence[Mapping[str, Any]],
    expected_workspace_root: str | Path,
) -> dict[str, Any]:
    try:
        fixture_id = _require_sha(expected_fixture_id, "expected fixture id")
        expected_file = _require_sha(expected_manifest_file_sha256, "expected fixture manifest file SHA")
        expected_receipt = _require_sha(expected_receipt_file_sha256, "expected fixture receipt file SHA")
        expected_composite = _require_sha(expected_composite_root_sha256, "expected composite root")
        expected_contract = _require_sha(expected_temporal_contract_sha256, "expected temporal contract")
        expected_producer = _require_sha(expected_producer_source_sha256, "expected producer source")
        expected_descriptor_file = _require_sha(
            expected_source_descriptor_file_sha256, "expected source descriptor file SHA"
        )
        workspace_root = _safe_directory(Path(expected_workspace_root), "caller trusted workspace")
        if expected_producer != fixture_producer_source_sha256():
            raise DevelopmentPayloadFixtureError("expected producer source does not match verifier source")
        expected_anchors = _required_source_anchors(expected_source_anchors, workspace_root)
        if any(anchor["temporal_contract_sha256"] != expected_contract for anchor in expected_anchors):
            raise DevelopmentPayloadFixtureError("expected source anchors use another temporal contract")
        root = _safe_directory(Path(fixture_root), "development payload fixture root")
        _require_within_workspace(root, workspace_root, "development payload fixture root")
        _reject_protected_lineage_path(root, "development payload fixture root")
        if root.name != fixture_id:
            raise DevelopmentPayloadFixtureError("fixture directory identity mismatch")
        descriptor, descriptor_raw, manifest, manifest_raw, receipt, receipt_raw = _read_fixture_files(root)
        if _sha_bytes(manifest_raw) != expected_file:
            raise DevelopmentPayloadFixtureError("fixture manifest file SHA mismatch")
        unsigned_manifest = {key: value for key, value in manifest.items() if key != "manifest_canonical_sha256"}
        if manifest.get("manifest_canonical_sha256") != _canonical_sha(unsigned_manifest):
            raise DevelopmentPayloadFixtureError("fixture manifest canonical SHA mismatch")
        identity_body = {key: value for key, value in unsigned_manifest.items() if key != "fixture_id"}
        if manifest.get("fixture_id") != _canonical_sha(identity_body) or manifest.get("fixture_id") != fixture_id:
            raise DevelopmentPayloadFixtureError("fixture content address mismatch")
        required_manifest = {
            "schema", "fixture_id", "temporal_role", "date_bounds", "composite_root_sha256",
            "temporal_contract_sha256", "producer_source_sha256", "payload_descriptor_file_sha256",
            "payload_descriptor_canonical_sha256", "source_segments_root_sha256", "source_segments",
            "manifest_canonical_sha256",
        }
        if set(manifest) != required_manifest or manifest.get("schema") != _FIXTURE_SCHEMA:
            raise DevelopmentPayloadFixtureError("fixture manifest fields are invalid")
        if _canonical_sha(descriptor) != manifest.get("payload_descriptor_canonical_sha256") or _sha_bytes(descriptor_raw) != manifest.get("payload_descriptor_file_sha256"):
            raise DevelopmentPayloadFixtureError("fixture payload descriptor mismatch")
        required_descriptor = {
            "schema", "temporal_role", "date_bounds", "composite_root_sha256",
            "temporal_contract_sha256", "producer_source_sha256", "source_descriptor",
            "source_segments", "source_segments_root_sha256",
        }
        if (
            set(descriptor) != required_descriptor
            or descriptor.get("schema") != _PAYLOAD_SCHEMA
            or descriptor.get("temporal_role") != "development"
        ):
            raise DevelopmentPayloadFixtureError("fixture payload schema or role is invalid")
        if descriptor.get("date_bounds") != {"start_date": _DATE_SEGMENTS[0][0], "end_date": _DATE_SEGMENTS[-1][1]}:
            raise DevelopmentPayloadFixtureError("fixture payload date bounds are invalid")
        source_descriptor = descriptor.get("source_descriptor")
        if (
            not isinstance(source_descriptor, Mapping)
            or set(source_descriptor) != {"bytes", "file_sha256"}
            or type(source_descriptor.get("bytes")) is not int
            or source_descriptor["bytes"] < 1
            or _require_sha(source_descriptor.get("file_sha256"), "fixture source descriptor SHA")
            != source_descriptor["file_sha256"]
            or source_descriptor["file_sha256"] != expected_descriptor_file
        ):
            raise DevelopmentPayloadFixtureError("fixture source descriptor binding is invalid")
        for key in ("temporal_role", "date_bounds", "composite_root_sha256", "temporal_contract_sha256", "producer_source_sha256"):
            if manifest.get(key) != descriptor.get(key):
                raise DevelopmentPayloadFixtureError("fixture manifest and payload differ")
        if (
            descriptor.get("composite_root_sha256") != expected_composite
            or descriptor.get("temporal_contract_sha256") != expected_contract
            or descriptor.get("producer_source_sha256") != expected_producer
        ):
            raise DevelopmentPayloadFixtureError("fixture external trusted root mismatch")
        segments = descriptor.get("source_segments")
        if not isinstance(segments, list) or len(segments) != 2 or manifest.get("source_segments") != segments:
            raise DevelopmentPayloadFixtureError("fixture source segments are invalid")
        if descriptor.get("source_segments_root_sha256") != _canonical_sha(segments) or manifest.get("source_segments_root_sha256") != _canonical_sha(segments):
            raise DevelopmentPayloadFixtureError("fixture source segment root mismatch")
        contract = _require_sha(descriptor.get("temporal_contract_sha256"), "fixture temporal contract")
        verified_segments = [
            _verify_segment_payload(item, index, contract, workspace_root)
            for index, item in enumerate(segments, 1)
        ]
        if verified_segments != expected_anchors:
            raise DevelopmentPayloadFixtureError("fixture source anchors differ from caller trusted anchors")
        if not hmac.compare_digest(_composite_root(verified_segments, contract), str(descriptor.get("composite_root_sha256") or "")):
            raise DevelopmentPayloadFixtureError("fixture composite root mismatch")
        required_receipt = {
            "schema", "fixture_id", "manifest_file_sha256", "manifest_canonical_sha256",
            "payload_files_root_sha256", "single_writer", "atomic_publish", "network_calls",
            "created_at_utc", "receipt_canonical_sha256",
        }
        unsigned_receipt = {key: value for key, value in receipt.items() if key != "receipt_canonical_sha256"}
        if _sha_bytes(receipt_raw) != expected_receipt:
            raise DevelopmentPayloadFixtureError("fixture receipt file SHA mismatch")
        if (
            set(receipt) != required_receipt
            or receipt.get("schema") != _RECEIPT_SCHEMA
            or receipt.get("fixture_id") != fixture_id
            or receipt.get("manifest_file_sha256") != _sha_bytes(manifest_raw)
            or receipt.get("manifest_canonical_sha256") != manifest.get("manifest_canonical_sha256")
            or receipt.get("single_writer") is not True
            or receipt.get("atomic_publish") is not True
            or receipt.get("network_calls") != 0
            or receipt.get("receipt_canonical_sha256") != _canonical_sha(unsigned_receipt)
        ):
            raise DevelopmentPayloadFixtureError("fixture receipt is invalid")
        tree = [
            {"path": "manifest.json", "sha256": _sha_bytes(manifest_raw)},
            {"path": "payload-descriptor.json", "sha256": _sha_bytes(descriptor_raw)},
        ]
        if receipt.get("payload_files_root_sha256") != _canonical_sha(tree):
            raise DevelopmentPayloadFixtureError("fixture payload tree root mismatch")
        return {
            "path": str(root),
            "fixture_id": fixture_id,
            "manifest_file_sha256": _sha_bytes(manifest_raw),
            "manifest_canonical_sha256": manifest["manifest_canonical_sha256"],
            "receipt_file_sha256": _sha_bytes(receipt_raw),
            "receipt_canonical_sha256": receipt["receipt_canonical_sha256"],
            "payload_files_root_sha256": receipt["payload_files_root_sha256"],
            "payload_descriptor_file_sha256": _sha_bytes(descriptor_raw),
            "payload_descriptor_canonical_sha256": _canonical_sha(descriptor),
            "source_descriptor_file_sha256": source_descriptor["file_sha256"],
            "temporal_role": "development",
            "date_bounds": descriptor["date_bounds"],
            "composite_root_sha256": descriptor["composite_root_sha256"],
            "temporal_contract_sha256": contract,
            "segments": verified_segments,
            "workspace_root": str(workspace_root),
        }
    except DevelopmentPayloadFixtureError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise DevelopmentPayloadFixtureError("development payload fixture rejected") from exc


def open_development_payload_loader(
    fixture_root: str | Path,
    **expected: Any,
) -> DevelopmentPayloadLoader:
    verified = verify_development_payload_fixture(fixture_root, **expected)
    return DevelopmentPayloadLoader(verified["segments"], Path(verified["workspace_root"]))


def publish_development_payload_fixture(
    parent: str | Path,
    *,
    source_descriptor_path: str | Path,
    expected_composite_root_sha256: str,
    expected_temporal_contract_sha256: str,
    producer_source_sha256: str,
    expected_source_descriptor_file_sha256: str,
    expected_source_anchors: Sequence[Mapping[str, Any]],
    expected_workspace_root: str | Path,
) -> dict[str, Any]:
    try:
        producer = _require_sha(producer_source_sha256, "fixture producer source hash")
        if producer != fixture_producer_source_sha256():
            raise DevelopmentPayloadFixtureError("fixture producer source hash mismatch")
        expected_descriptor_file = _require_sha(
            expected_source_descriptor_file_sha256, "expected source descriptor file SHA"
        )
        workspace_root = _safe_directory(Path(expected_workspace_root), "caller trusted workspace")
        expected_anchors = _required_source_anchors(expected_source_anchors, workspace_root)
        _descriptor, source_raw, segments = _read_source_descriptor(
            source_descriptor_path,
            expected_composite_root_sha256=expected_composite_root_sha256,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            workspace_root=workspace_root,
        )
        if _sha_bytes(source_raw) != expected_descriptor_file:
            raise DevelopmentPayloadFixtureError("source descriptor external hash mismatch")
        if segments != expected_anchors:
            raise DevelopmentPayloadFixtureError("source descriptor differs from caller trusted anchors")
        payload = _payload_descriptor(
            source_descriptor_raw=source_raw,
            segments=segments,
            composite_root_sha256=expected_composite_root_sha256,
            temporal_contract_sha256=expected_temporal_contract_sha256,
            producer_source_sha256=producer,
        )
        payload_raw = _canonical_bytes(payload) + b"\n"
        body = {
            "schema": _FIXTURE_SCHEMA,
            "temporal_role": "development",
            "date_bounds": payload["date_bounds"],
            "composite_root_sha256": expected_composite_root_sha256,
            "temporal_contract_sha256": expected_temporal_contract_sha256,
            "producer_source_sha256": producer,
            "payload_descriptor_file_sha256": _sha_bytes(payload_raw),
            "payload_descriptor_canonical_sha256": _canonical_sha(payload),
            "source_segments_root_sha256": payload["source_segments_root_sha256"],
            "source_segments": segments,
        }
        fixture_id = _canonical_sha(body)
        manifest = {**body, "fixture_id": fixture_id}
        manifest["manifest_canonical_sha256"] = _canonical_sha(manifest)
        manifest_raw = _canonical_bytes(manifest) + b"\n"
        tree = [
            {"path": "manifest.json", "sha256": _sha_bytes(manifest_raw)},
            {"path": "payload-descriptor.json", "sha256": _sha_bytes(payload_raw)},
        ]
        receipt = {
            "schema": _RECEIPT_SCHEMA,
            "fixture_id": fixture_id,
            "manifest_file_sha256": _sha_bytes(manifest_raw),
            "manifest_canonical_sha256": manifest["manifest_canonical_sha256"],
            "payload_files_root_sha256": _canonical_sha(tree),
            "single_writer": True,
            "atomic_publish": True,
            "network_calls": 0,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        receipt["receipt_canonical_sha256"] = _canonical_sha(receipt)
        receipt_raw = _canonical_bytes(receipt) + b"\n"
        parent_path = _require_declared_within_workspace(
            Path(parent), workspace_root, "fixture parent"
        )
        _reject_protected_lineage_path(parent_path, "fixture parent")
        parent_path.mkdir(parents=True, exist_ok=True)
        parent_path = _safe_directory(parent_path, "fixture parent")
        _require_within_workspace(parent_path, workspace_root, "fixture parent")
        _reject_protected_lineage_path(parent_path, "fixture parent")
        target = parent_path / fixture_id
        _reject_protected_lineage_path(target, "fixture target")
        if target.exists():
            verified = verify_development_payload_fixture(
                target,
                expected_fixture_id=fixture_id,
                expected_manifest_file_sha256=_sha_bytes(manifest_raw),
                expected_receipt_file_sha256=_sha_bytes(receipt_raw),
                expected_composite_root_sha256=expected_composite_root_sha256,
                expected_temporal_contract_sha256=expected_temporal_contract_sha256,
                expected_producer_source_sha256=producer,
                expected_source_descriptor_file_sha256=expected_descriptor_file,
                expected_source_anchors=expected_anchors,
                expected_workspace_root=workspace_root,
            )
            return {**verified, "source_segments": segments}
        staging = parent_path / f".development-payload-fixture-{fixture_id}-{uuid.uuid4().hex}.staging"
        staging.mkdir()
        _write_new(staging / "payload-descriptor.json", payload_raw)
        _write_new(staging / "manifest.json", manifest_raw)
        _write_new(staging / "receipt.json", receipt_raw)
        fsync_directory(staging)
        os.rename(staging, target)
        fsync_directory(parent_path)
        verified = verify_development_payload_fixture(
            target,
            expected_fixture_id=fixture_id,
            expected_manifest_file_sha256=_sha_bytes(manifest_raw),
            expected_receipt_file_sha256=_sha_bytes(receipt_raw),
            expected_composite_root_sha256=expected_composite_root_sha256,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            expected_producer_source_sha256=producer,
            expected_source_descriptor_file_sha256=expected_descriptor_file,
            expected_source_anchors=expected_anchors,
            expected_workspace_root=workspace_root,
        )
        return {**verified, "source_segments": segments}
    except DevelopmentPayloadFixtureError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise DevelopmentPayloadFixtureError("development payload fixture publication failed") from exc
