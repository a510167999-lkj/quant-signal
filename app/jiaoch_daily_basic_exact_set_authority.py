"""Offline exact-set authority for audited daily and Jiaoch daily_basic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid

from app import jiaoch_points_collection_set as points_collection
from app import jiaoch_daily_basic_collection_set as daily_basic_collection
from app import jiaoch_points_raw_authority as raw_authority
from app import research_security_code_transition as code_transition
from app.jiaoch_points_response_normalization import (
    NormalizedDailyBasicRow,
    normalize_jiaoch_points_response_preview,
)
from app.research_pit_store import AuditedPointInTimeUniverse
from app.research_scope import POLICY_ID


__all__ = (
    "publish_daily_basic_exact_set_coverage",
    "verify_daily_basic_exact_set_coverage",
)

RECEIPT_SCHEMA = "jiaoch-daily-basic-exact-set-coverage/v1"
PRODUCER_VERSION = "app.jiaoch_daily_basic_exact_set_authority/1"

_AUTHORITY_STATUS = "VERIFIED_DEVELOPMENT_DAILY_BASIC_EXACT_SET"
_ROW_AUTHORITY_STATUS = "GRANTED_FOR_BOUND_DEVELOPMENT_COVERAGE_ONLY"
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_RAW_BODY_BYTES = 32 * 1024 * 1024
_MAX_PRODUCER_BYTES = 4 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RECEIPT_PATH_PATTERN = re.compile(
    r"daily_basic_exact_set_coverage_candidates/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json"
)
_COLLECTION_PATH_PATTERNS = (
    re.compile(r"points_collection_sets/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json"),
    re.compile(r"daily_basic_collection_sets/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json"),
)
_SUPPORTED_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
_TARGET_SEGMENTS = frozenset({"SSE_MAIN", "SZSE_CHINEXT", "SZSE_MAIN"})
_OVERLAP_FIELDS = (
    "turnover_rate",
    "turnover_rate_f",
    "free_share",
    "float_share",
    "total_mv",
    "circ_mv",
)
_TRANSITION_FIELDS = frozenset(
    {
        "canonical_ts_code",
        "effective_date",
        "predecessor_ts_code",
        "security_id",
        "successor_ts_code",
        "transition_id",
    }
)
_COLLECTION_REF_FIELDS = frozenset(
    {
        "collection_set_relative_path",
        "collection_set_sha256",
        "trade_date",
    }
)
_PER_DATE_FIELDS = frozenset(
    {
        "authoritative_daily_filtered_codes_sha256",
        "authoritative_daily_generation_id",
        "authoritative_daily_generation_lineage_sha256",
        "authoritative_daily_generation_manifest_sha256",
        "authoritative_daily_raw_codes_sha256",
        "authoritative_daily_raw_row_count",
        "authoritative_daily_raw_segment_counts",
        "authoritative_daily_resolved_identities_sha256",
        "authoritative_daily_transition_excluded_codes_sha256",
        "authoritative_daily_transition_excluded_row_count",
        "authoritative_daily_vintage",
        "collection_set_relative_path",
        "collection_set_sha256",
        "daily_basic_attempt_relative_path",
        "daily_basic_attempt_sha256",
        "daily_basic_canonical_rows_sha256",
        "daily_basic_filtered_codes_sha256",
        "daily_basic_normalization_receipt_sha256",
        "daily_basic_raw_codes_sha256",
        "daily_basic_raw_relative_path",
        "daily_basic_raw_row_count",
        "daily_basic_raw_segment_counts",
        "daily_basic_raw_sha256",
        "daily_basic_resolved_identities_sha256",
        "daily_basic_source_normalization_rows_sha256",
        "daily_basic_transition_excluded_codes_sha256",
        "daily_basic_transition_excluded_row_count",
        "daily_basic_transition_overlap_comparison_fields",
        "daily_basic_transition_overlap_pair_count",
        "daily_basic_transition_overlap_root_sha256",
        "extra_daily_basic_code_count",
        "missing_authoritative_daily_code_count",
        "source_ts_code_exact_set_verified_after_transition_filter",
        "target_scope_codes_sha256",
        "target_scope_resolved_identities_sha256",
        "target_scope_row_count",
        "trade_date",
        "transition_filtered_row_count",
        "transition_resolved_identity_exact_set_verified",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "all_supported_segments_compared_before_scope_filter",
        "arbitrary_row_drops_permitted",
        "audited_daily_authority",
        "authority_root_sha256",
        "authority_scope",
        "authority_status",
        "embargo_consumed",
        "exact_set_verified",
        "factor_v3_development_materialization_input_eligible",
        "factor_v3_target_identity_root_sha256",
        "factor_v3_target_scope",
        "final_oos_consumed",
        "formal_factor_v3_materialization_performed",
        "normalized_daily_basic_row_authority_root_sha256",
        "per_date_statistics",
        "per_date_statistics_sha256",
        "producer_binding",
        "publication_capability_sha256",
        "production_profile_registered",
        "production_recommendation_eligible",
        "raw_source_rows_bound",
        "row_authority_status",
        "rows_published",
        "schema",
        "security_code_transition_authority",
        "silent_row_drops_permitted",
        "source_binding_root_sha256",
        "source_missingness",
        "source_ts_code_exact_set_verified_after_transition_filter",
        "trade_date_count",
        "trade_dates",
        "trade_dates_sha256",
        "transition_boundary_authority_root_sha256",
        "transition_boundary_count",
        "transition_overlap_authority_root_sha256",
        "transition_resolved_identity_exact_set_verified",
    }
)
_POLICY = {
    "authorized_transition_exclusions": [
        "successor_before_effective_date",
        "predecessor_on_or_after_effective_date",
    ],
    "code_comparison_layers": [
        "source_ts_code_after_authorized_transition_filter",
        "transition_resolved_security_identity",
    ],
    "collection_date_policy": "exactly_all_input_audited_market_generation_refs",
    "factor_overlap_fields": list(_OVERLAP_FIELDS),
    "market_scope_policy_id": POLICY_ID,
    "missingness_policy": "whole_coverage_fail_closed",
    "raw_segment_policy": "compare_main_chinext_star_bse_before_target_scope",
    "schema": "jiaoch-daily-basic-exact-set-policy/v1",
    "selective_candidate_exclusion_permitted": False,
}
_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _POLICY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()
_PRODUCER_FILES = (
    "jiaoch_daily_basic_exact_set_authority.py",
    "jiaoch_daily_basic_collection_set.py",
    "jiaoch_points_collection_set.py",
    "jiaoch_points_raw_authority.py",
    "jiaoch_points_response_normalization.py",
    "research_pit_store.py",
    "research_scope.py",
    "research_security_code_transition.py",
)


@dataclass(frozen=True)
class AuthoritativeDailyPartition:
    trade_date: str
    generation_id: str
    generation_manifest_sha256: str
    generation_lineage_sha256: str
    vintage: str
    ts_codes: tuple[str, ...]


@dataclass(frozen=True)
class AuditedDailyAuthority:
    manifest_file_sha256: str
    manifest_sha256: str
    bundle_sha256: str
    artifact_root_sha256: str
    sqlite_sha256: str
    coverage_audit_sha256: str
    temporal_contract_sha256: str
    temporal_role: str
    daily_table_rows: int
    daily_table_sha256: str
    market_generation_count: int
    market_generation_root_sha256: str
    partitions: tuple[AuthoritativeDailyPartition, ...]


@dataclass(frozen=True)
class DailyBasicPartition:
    trade_date: str
    collection_set_relative_path: str
    collection_set_sha256: str
    attempt_relative_path: str
    attempt_sha256: str
    raw_relative_path: str
    raw_sha256: str
    source_normalization_rows_sha256: str
    canonical_rows_sha256: str
    normalization_receipt_sha256: str
    rows: tuple[NormalizedDailyBasicRow, ...]


@dataclass(frozen=True)
class TransitionAuthority:
    contract_sha256: str
    evidence_receipt_sha256: str
    evidence_receipts_sha256: str
    transition_count: int
    transitions_by_code: Mapping[str, Mapping[str, Any]]


class _JsonObjectPairs(list):
    pass


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("daily_basic exact-set canonical JSON rejected") from None


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("daily_basic exact-set duplicate JSON key rejected")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("daily_basic exact-set non-finite JSON rejected")


def _strict_json_loads(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("daily_basic exact-set JSON rejected") from None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _sha256_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    return value


def _strict_positive_int(value: Any, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} rejected")
    return value


def _strict_nonnegative_int(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} rejected")
    return value


def _publication_capability(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("daily_basic exact-set publication capability rejected")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        raise ValueError("daily_basic exact-set publication capability rejected") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("daily_basic exact-set publication capability rejected")
    return value


def _trade_date(value: Any, *, label: str = "trade_date") -> str:
    if type(value) is not str:
        raise ValueError(f"{label} rejected")
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} rejected") from None
    if normalized != value:
        raise ValueError(f"{label} rejected")
    return normalized


def _aware_timestamp(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} rejected")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{label} rejected") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} rejected")
    return value


def _market_segment(value: Any) -> tuple[str, bool]:
    if type(value) is not str:
        raise ValueError("daily_basic exact-set ts_code rejected")
    if re.fullmatch(r"(?:600|601|603|605)[0-9]{3}\.SH", value):
        return "SSE_MAIN", True
    if re.fullmatch(r"(?:688|689)[0-9]{3}\.SH", value):
        return "SSE_STAR", False
    if re.fullmatch(r"(?:000|001|002|003)[0-9]{3}\.SZ", value):
        return "SZSE_MAIN", True
    if re.fullmatch(r"(?:300|301|302)[0-9]{3}\.SZ", value):
        return "SZSE_CHINEXT", True
    if re.fullmatch(r"(?:4|8|9)[0-9]{5}\.BJ", value):
        return "BSE", False
    raise ValueError("daily_basic exact-set ts_code rejected")


def _segment_counts(codes: Sequence[str]) -> dict[str, int]:
    result = {segment: 0 for segment in _SUPPORTED_SEGMENTS}
    for code in codes:
        segment, _in_scope = _market_segment(code)
        result[segment] += 1
    return result


def _producer_binding() -> dict[str, Any]:
    source_root = raw_authority._safe_existing_directory(
        Path(__file__).parent,
        "daily_basic exact-set producer root",
    )
    entries = []
    for filename in _PRODUCER_FILES:
        source = raw_authority._read_safe_file(
            source_root / filename,
            label="daily_basic exact-set producer source",
            max_bytes=_MAX_PRODUCER_BYTES,
        )
        entries.append({"path": f"app/{filename}", "sha256": _sha256(source)})
    identity = {
        "entries": entries,
        "policy_sha256": _POLICY_SHA256,
        "producer_version": PRODUCER_VERSION,
        "schema": "jiaoch-daily-basic-exact-set-producer/v2",
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


def _validate_transition_authority(
    value: TransitionAuthority,
) -> dict[str, Mapping[str, Any]]:
    if type(value) is not TransitionAuthority:
        raise ValueError("security-code transition authority rejected")
    _sha256_text(value.contract_sha256, label="transition contract sha256")
    _sha256_text(value.evidence_receipt_sha256, label="transition evidence receipt sha256")
    _sha256_text(value.evidence_receipts_sha256, label="transition evidence rows sha256")
    _strict_positive_int(value.transition_count, label="transition count")
    if type(value.transitions_by_code) is not dict:
        raise ValueError("security-code transition lookup rejected")
    normalized: dict[str, Mapping[str, Any]] = {}
    transition_ids: set[str] = set()
    for code, raw in value.transitions_by_code.items():
        _market_segment(code)
        if type(raw) is not dict or set(raw) != _TRANSITION_FIELDS:
            raise ValueError("security-code transition descriptor rejected")
        predecessor = str(raw["predecessor_ts_code"])
        successor = str(raw["successor_ts_code"])
        canonical = str(raw["canonical_ts_code"])
        _market_segment(predecessor)
        _market_segment(successor)
        if canonical != predecessor or code not in {predecessor, successor}:
            raise ValueError("security-code transition identity rejected")
        effective = _trade_date(raw["effective_date"], label="transition effective_date")
        transition_id = _sha256_text(raw["transition_id"], label="transition transition_id")
        security_id = raw["security_id"]
        if (
            predecessor == successor
            or type(security_id) is not str
            or not security_id.startswith("cn-a-share:")
        ):
            raise ValueError("security-code transition identity rejected")
        descriptor = {
            "canonical_ts_code": canonical,
            "effective_date": effective,
            "predecessor_ts_code": predecessor,
            "security_id": security_id,
            "successor_ts_code": successor,
            "transition_id": transition_id,
        }
        existing = normalized.get(code)
        if existing is not None and existing != descriptor:
            raise ValueError("security-code transition identity is ambiguous")
        normalized[code] = descriptor
        transition_ids.add(transition_id)
    for descriptor in tuple(normalized.values()):
        if (
            normalized.get(str(descriptor["predecessor_ts_code"])) != descriptor
            or normalized.get(str(descriptor["successor_ts_code"])) != descriptor
        ):
            raise ValueError("security-code transition lookup is incomplete")
    if len(transition_ids) != value.transition_count:
        raise ValueError("security-code transition count rejected")
    return normalized


def _validate_audited_daily_authority(
    value: AuditedDailyAuthority,
) -> tuple[AuthoritativeDailyPartition, ...]:
    if type(value) is not AuditedDailyAuthority:
        raise ValueError("audited daily authority rejected")
    for label, digest in (
        ("audited manifest file sha256", value.manifest_file_sha256),
        ("audited manifest sha256", value.manifest_sha256),
        ("audited bundle sha256", value.bundle_sha256),
        ("audited artifact root sha256", value.artifact_root_sha256),
        ("audited sqlite sha256", value.sqlite_sha256),
        ("audited coverage sha256", value.coverage_audit_sha256),
        ("audited temporal contract sha256", value.temporal_contract_sha256),
        ("audited daily table sha256", value.daily_table_sha256),
        ("audited market generation root sha256", value.market_generation_root_sha256),
    ):
        _sha256_text(digest, label=label)
    if value.temporal_role != "development":
        raise ValueError("audited daily temporal role rejected")
    row_count = _strict_positive_int(value.daily_table_rows, label="audited daily rows")
    generation_count = _strict_positive_int(
        value.market_generation_count,
        label="audited market generation count",
    )
    if type(value.partitions) is not tuple or len(value.partitions) != generation_count:
        raise ValueError("audited market generation refs rejected")
    dates: list[str] = []
    rows = 0
    refs: list[dict[str, Any]] = []
    for partition in value.partitions:
        if type(partition) is not AuthoritativeDailyPartition:
            raise ValueError("audited daily partition rejected")
        session = _trade_date(partition.trade_date)
        if type(partition.generation_id) is not str or not partition.generation_id:
            raise ValueError("audited daily generation_id rejected")
        _sha256_text(
            partition.generation_manifest_sha256,
            label="audited generation manifest sha256",
        )
        _sha256_text(
            partition.generation_lineage_sha256,
            label="audited generation lineage sha256",
        )
        _aware_timestamp(partition.vintage, label="audited generation vintage")
        if type(partition.ts_codes) is not tuple or not partition.ts_codes:
            raise ValueError("audited daily codes rejected")
        codes = list(partition.ts_codes)
        if codes != sorted(set(codes)):
            raise ValueError("audited daily duplicate or unordered ts_code")
        for code in codes:
            _market_segment(code)
        dates.append(session)
        rows += len(codes)
        refs.append(
            {
                "generation_id": partition.generation_id,
                "lineage_sha256": partition.generation_lineage_sha256,
                "manifest_sha256": partition.generation_manifest_sha256,
                "trade_date": session,
                "vintage": partition.vintage,
            }
        )
    if dates != sorted(set(dates)):
        raise ValueError("audited market generation dates rejected")
    if rows != row_count:
        raise ValueError("audited daily table row count rejected")
    if not hmac.compare_digest(
        _canonical_sha256(refs),
        value.market_generation_root_sha256,
    ):
        raise ValueError("audited market generation root rejected")
    return value.partitions


def _validated_collection_refs(
    refs: Sequence[Mapping[str, Any]],
    *,
    required_dates: Sequence[str],
) -> tuple[dict[str, str], ...]:
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise ValueError("collection ref dates rejected")
    normalized: list[dict[str, str]] = []
    for raw in refs:
        if type(raw) is not dict or set(raw) != _COLLECTION_REF_FIELDS:
            raise ValueError("collection ref descriptor rejected")
        session = _trade_date(raw.get("trade_date"), label="collection trade date")
        digest = _sha256_text(
            raw.get("collection_set_sha256"),
            label="collection set sha256",
        )
        relative = raw.get("collection_set_relative_path")
        if type(relative) is not str:
            raise ValueError("collection set path rejected")
        matches = [pattern.fullmatch(relative) for pattern in _COLLECTION_PATH_PATTERNS]
        if not any(
            match is not None and match.group(1) == digest[:2] and match.group(2) == digest
            for match in matches
        ):
            raise ValueError("collection set path rejected")
        normalized.append(
            {
                "collection_set_relative_path": relative,
                "collection_set_sha256": digest,
                "trade_date": session,
            }
        )
    dates = [item["trade_date"] for item in normalized]
    if dates != list(required_dates):
        raise ValueError("collection ref date sequence does not match audited trade dates")
    if len(dates) != len(set(dates)):
        raise ValueError("collection ref dates contain duplicates")
    return tuple(normalized)


def _validate_daily_basic_partition(
    value: DailyBasicPartition,
    *,
    collection_ref: Mapping[str, str],
) -> tuple[NormalizedDailyBasicRow, ...]:
    if type(value) is not DailyBasicPartition:
        raise ValueError("daily_basic partition authority rejected")
    session = _trade_date(value.trade_date, label="daily_basic trade_date")
    if (
        session != collection_ref["trade_date"]
        or value.collection_set_relative_path != collection_ref["collection_set_relative_path"]
        or value.collection_set_sha256 != collection_ref["collection_set_sha256"]
    ):
        raise ValueError("daily_basic collection binding rejected")
    for label, digest in (
        ("daily_basic collection set sha256", value.collection_set_sha256),
        ("daily_basic attempt sha256", value.attempt_sha256),
        ("daily_basic raw sha256", value.raw_sha256),
        (
            "daily_basic source normalization rows sha256",
            value.source_normalization_rows_sha256,
        ),
        ("daily_basic canonical rows sha256", value.canonical_rows_sha256),
        ("daily_basic normalization receipt sha256", value.normalization_receipt_sha256),
    ):
        _sha256_text(digest, label=label)
    for label, relative in (
        ("daily_basic attempt path", value.attempt_relative_path),
        ("daily_basic raw path", value.raw_relative_path),
    ):
        if type(relative) is not str or not relative:
            raise ValueError(f"{label} rejected")
    if type(value.rows) is not tuple or not value.rows:
        raise ValueError("daily_basic rows rejected")
    identities: list[str] = []
    canonical_rows: list[dict[str, Any]] = []
    for row in value.rows:
        if type(row) is not NormalizedDailyBasicRow:
            raise ValueError("daily_basic normalized row rejected")
        if row.trade_date != session:
            raise ValueError("daily_basic row trade_date rejected")
        segment, in_scope = _market_segment(row.ts_code)
        if row.market_segment != segment:
            raise ValueError("daily_basic normalized market segment rejected")
        if row.research_scope_mainboard_chinext is not in_scope:
            raise ValueError("daily_basic normalized research scope rejected")
        for field in _OVERLAP_FIELDS:
            number = getattr(row, field)
            if type(number) is not float or not math.isfinite(number) or number < 0.0:
                raise ValueError(f"daily_basic normalized {field} rejected")
        identities.append(row.ts_code)
        canonical_rows.append(asdict(row))
    if identities != sorted(set(identities)):
        raise ValueError("daily_basic duplicate or unordered identity rejected")
    if not hmac.compare_digest(
        _canonical_sha256(canonical_rows),
        value.canonical_rows_sha256,
    ):
        raise ValueError("daily_basic canonical rows root rejected")
    return value.rows


def _same_number(left: float, right: float) -> bool:
    return left == right


def _daily_basic_overlap_proofs(
    rows: Sequence[NormalizedDailyBasicRow],
    *,
    transitions_by_code: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_code = {row.ts_code: row for row in rows}
    proofs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for descriptor in transitions_by_code.values():
        transition_id = str(descriptor["transition_id"])
        if transition_id in seen:
            continue
        seen.add(transition_id)
        predecessor = str(descriptor["predecessor_ts_code"])
        successor = str(descriptor["successor_ts_code"])
        if predecessor not in by_code or successor not in by_code:
            continue
        left = by_code[predecessor]
        right = by_code[successor]
        for field in _OVERLAP_FIELDS:
            if not _same_number(getattr(left, field), getattr(right, field)):
                raise ValueError(f"daily_basic transition overlap {field} conflict")
        values = {field: getattr(left, field) for field in _OVERLAP_FIELDS}
        proofs.append(
            {
                "comparison_fields": list(_OVERLAP_FIELDS),
                "effective_date": descriptor["effective_date"],
                "predecessor_ts_code": predecessor,
                "successor_ts_code": successor,
                "transition_id": transition_id,
                "values_sha256": _canonical_sha256(values),
            }
        )
    return sorted(proofs, key=lambda item: str(item["transition_id"]))


def _transition_filter_codes(
    codes: Sequence[str],
    *,
    trade_date: str,
    transitions_by_code: Mapping[str, Mapping[str, Any]],
    source_label: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    filtered: list[str] = []
    identities: list[str] = []
    excluded: list[str] = []
    source_codes = set(codes)
    for code in codes:
        descriptor = transitions_by_code.get(code)
        if descriptor is None:
            filtered.append(code)
            identities.append(f"cn-a-share:{code}")
            continue
        expected = (
            str(descriptor["predecessor_ts_code"])
            if trade_date < str(descriptor["effective_date"])
            else str(descriptor["successor_ts_code"])
        )
        if code != expected:
            if expected not in source_codes:
                raise ValueError(
                    f"{source_label} transition wrong-period alias lacks correct counterpart"
                )
            excluded.append(code)
            continue
        filtered.append(code)
        identities.append(str(descriptor["security_id"]))
    if len(filtered) != len(set(filtered)):
        raise ValueError("transition filtered source ts_code contains duplicate")
    if len(identities) != len(set(identities)):
        raise ValueError("transition resolved identity contains duplicate")
    ordered = sorted(zip(filtered, identities, strict=True))
    return (
        tuple(code for code, _identity in ordered),
        tuple(identity for _code, identity in ordered),
        tuple(sorted(excluded)),
    )


def _validate_transition_boundaries(
    *,
    trade_dates: Sequence[str],
    filtered_codes_by_date: Mapping[str, Sequence[str]],
    transitions_by_code: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    proofs: list[dict[str, Any]] = []
    seen: set[str] = set()
    first = trade_dates[0]
    last = trade_dates[-1]
    for descriptor in transitions_by_code.values():
        transition_id = str(descriptor["transition_id"])
        if transition_id in seen:
            continue
        seen.add(transition_id)
        effective = str(descriptor["effective_date"])
        predecessor = str(descriptor["predecessor_ts_code"])
        successor = str(descriptor["successor_ts_code"])
        predecessor_boundary: str | None = None
        if first < effective <= last:
            if effective not in filtered_codes_by_date:
                raise ValueError("security-code transition boundary session is missing")
            index = list(trade_dates).index(effective)
            predecessor_boundary = trade_dates[index - 1]
            if predecessor not in filtered_codes_by_date[predecessor_boundary]:
                raise ValueError("security-code transition boundary predecessor is missing")
            if successor not in filtered_codes_by_date[effective]:
                raise ValueError("security-code transition boundary successor is missing")
        elif first == effective:
            if successor not in filtered_codes_by_date[effective]:
                raise ValueError("security-code transition boundary successor is missing")
        else:
            continue
        proofs.append(
            {
                "effective_date": effective,
                "predecessor_boundary_date": predecessor_boundary,
                "predecessor_ts_code": predecessor,
                "security_id": descriptor["security_id"],
                "successor_ts_code": successor,
                "transition_id": transition_id,
            }
        )
    return sorted(proofs, key=lambda item: str(item["transition_id"]))


def _authority_descriptors(
    daily: AuditedDailyAuthority,
    transition: TransitionAuthority,
) -> tuple[dict[str, Any], dict[str, Any]]:
    daily_descriptor = {
        "artifact_root_sha256": daily.artifact_root_sha256,
        "bundle_sha256": daily.bundle_sha256,
        "coverage_audit_sha256": daily.coverage_audit_sha256,
        "daily_table_rows": daily.daily_table_rows,
        "daily_table_sha256": daily.daily_table_sha256,
        "manifest_file_sha256": daily.manifest_file_sha256,
        "manifest_sha256": daily.manifest_sha256,
        "market_generation_count": daily.market_generation_count,
        "market_generation_root_sha256": daily.market_generation_root_sha256,
        "sqlite_sha256": daily.sqlite_sha256,
        "temporal_contract_sha256": daily.temporal_contract_sha256,
        "temporal_role": daily.temporal_role,
    }
    transition_descriptor = {
        "contract_sha256": transition.contract_sha256,
        "evidence_receipt_sha256": transition.evidence_receipt_sha256,
        "evidence_receipts_sha256": transition.evidence_receipts_sha256,
        "transition_count": transition.transition_count,
    }
    return daily_descriptor, transition_descriptor


def _derive_receipt(
    *,
    audited_universe_sqlite_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    expected_temporal_contract_sha256: str,
    expected_temporal_role: str,
    points_output_root: str | Path,
    collection_set_refs: Sequence[Mapping[str, Any]],
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    publication_capability_sha256: str,
) -> dict[str, Any]:
    capability_sha256 = _sha256_text(
        publication_capability_sha256,
        label="daily_basic exact-set publication capability sha256",
    )
    daily = _load_audited_daily_authority(
        audited_universe_sqlite_path=audited_universe_sqlite_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_temporal_contract_sha256,
        expected_temporal_role=expected_temporal_role,
    )
    partitions = _validate_audited_daily_authority(daily)
    trade_dates = [partition.trade_date for partition in partitions]
    refs = _validated_collection_refs(
        collection_set_refs,
        required_dates=trade_dates,
    )
    transition = _load_transition_authority(
        security_code_transition_evidence_root=(security_code_transition_evidence_root),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
    )
    transitions_by_code = _validate_transition_authority(transition)

    per_date: list[dict[str, Any]] = []
    target_identity_rows: list[dict[str, Any]] = []
    normalized_authority_rows: list[dict[str, Any]] = []
    overlap_authority_rows: list[dict[str, Any]] = []
    daily_identity_rows: list[dict[str, Any]] = []
    filtered_codes_by_date: dict[str, tuple[str, ...]] = {}
    target_total = 0
    for daily_partition, collection_ref in zip(partitions, refs, strict=True):
        session = daily_partition.trade_date
        daily_basic = _load_daily_basic_partition(
            points_output_root=points_output_root,
            collection_ref=collection_ref,
        )
        rows = _validate_daily_basic_partition(
            daily_basic,
            collection_ref=collection_ref,
        )
        daily_raw_codes = daily_partition.ts_codes
        basic_raw_codes = tuple(row.ts_code for row in rows)
        overlap_proofs = _daily_basic_overlap_proofs(
            rows,
            transitions_by_code=transitions_by_code,
        )
        daily_filtered, daily_identities, daily_excluded = _transition_filter_codes(
            daily_raw_codes,
            trade_date=session,
            transitions_by_code=transitions_by_code,
            source_label="authoritative daily",
        )
        basic_filtered, basic_identities, basic_excluded = _transition_filter_codes(
            basic_raw_codes,
            trade_date=session,
            transitions_by_code=transitions_by_code,
            source_label="daily_basic",
        )
        missing = sorted(set(daily_filtered) - set(basic_filtered))
        extra = sorted(set(basic_filtered) - set(daily_filtered))
        if missing or extra:
            raise ValueError(
                "daily and daily_basic source ts_code exact set failed after transition filter"
            )
        if daily_identities != basic_identities:
            raise ValueError("daily and daily_basic resolved identity exact set failed")
        target_pairs = [
            (code, identity)
            for code, identity in zip(basic_filtered, basic_identities, strict=True)
            if _market_segment(code)[0] in _TARGET_SEGMENTS
        ]
        target_codes = [code for code, _identity in target_pairs]
        target_identities = [identity for _code, identity in target_pairs]
        target_total += len(target_pairs)
        filtered_codes_by_date[session] = daily_filtered
        overlap_root = _canonical_sha256(overlap_proofs)
        stats = {
            "authoritative_daily_filtered_codes_sha256": _canonical_sha256(list(daily_filtered)),
            "authoritative_daily_generation_id": daily_partition.generation_id,
            "authoritative_daily_generation_lineage_sha256": (
                daily_partition.generation_lineage_sha256
            ),
            "authoritative_daily_generation_manifest_sha256": (
                daily_partition.generation_manifest_sha256
            ),
            "authoritative_daily_raw_codes_sha256": _canonical_sha256(list(daily_raw_codes)),
            "authoritative_daily_raw_row_count": len(daily_raw_codes),
            "authoritative_daily_raw_segment_counts": _segment_counts(daily_raw_codes),
            "authoritative_daily_resolved_identities_sha256": _canonical_sha256(
                list(daily_identities)
            ),
            "authoritative_daily_transition_excluded_codes_sha256": (
                _canonical_sha256(list(daily_excluded))
            ),
            "authoritative_daily_transition_excluded_row_count": len(daily_excluded),
            "authoritative_daily_vintage": daily_partition.vintage,
            "collection_set_relative_path": daily_basic.collection_set_relative_path,
            "collection_set_sha256": daily_basic.collection_set_sha256,
            "daily_basic_attempt_relative_path": daily_basic.attempt_relative_path,
            "daily_basic_attempt_sha256": daily_basic.attempt_sha256,
            "daily_basic_canonical_rows_sha256": daily_basic.canonical_rows_sha256,
            "daily_basic_filtered_codes_sha256": _canonical_sha256(list(basic_filtered)),
            "daily_basic_normalization_receipt_sha256": (daily_basic.normalization_receipt_sha256),
            "daily_basic_raw_codes_sha256": _canonical_sha256(list(basic_raw_codes)),
            "daily_basic_raw_relative_path": daily_basic.raw_relative_path,
            "daily_basic_raw_row_count": len(basic_raw_codes),
            "daily_basic_raw_segment_counts": _segment_counts(basic_raw_codes),
            "daily_basic_raw_sha256": daily_basic.raw_sha256,
            "daily_basic_resolved_identities_sha256": _canonical_sha256(list(basic_identities)),
            "daily_basic_source_normalization_rows_sha256": (
                daily_basic.source_normalization_rows_sha256
            ),
            "daily_basic_transition_excluded_codes_sha256": _canonical_sha256(list(basic_excluded)),
            "daily_basic_transition_excluded_row_count": len(basic_excluded),
            "daily_basic_transition_overlap_comparison_fields": list(_OVERLAP_FIELDS),
            "daily_basic_transition_overlap_pair_count": len(overlap_proofs),
            "daily_basic_transition_overlap_root_sha256": overlap_root,
            "extra_daily_basic_code_count": 0,
            "missing_authoritative_daily_code_count": 0,
            "source_ts_code_exact_set_verified_after_transition_filter": True,
            "target_scope_codes_sha256": _canonical_sha256(target_codes),
            "target_scope_resolved_identities_sha256": _canonical_sha256(target_identities),
            "target_scope_row_count": len(target_pairs),
            "trade_date": session,
            "transition_filtered_row_count": len(basic_filtered),
            "transition_resolved_identity_exact_set_verified": True,
        }
        per_date.append(stats)
        daily_identity_rows.append(
            {
                "filtered_codes_sha256": stats["authoritative_daily_filtered_codes_sha256"],
                "generation_id": daily_partition.generation_id,
                "raw_codes_sha256": stats["authoritative_daily_raw_codes_sha256"],
                "trade_date": session,
            }
        )
        normalized_authority_rows.append(
            {
                "canonical_rows_sha256": daily_basic.canonical_rows_sha256,
                "collection_set_sha256": daily_basic.collection_set_sha256,
                "raw_sha256": daily_basic.raw_sha256,
                "source_normalization_rows_sha256": (daily_basic.source_normalization_rows_sha256),
                "trade_date": session,
            }
        )
        overlap_authority_rows.append(
            {
                "overlap_pair_count": len(overlap_proofs),
                "overlap_root_sha256": overlap_root,
                "trade_date": session,
            }
        )
        target_identity_rows.append(
            {
                "resolved_identities_sha256": stats["target_scope_resolved_identities_sha256"],
                "row_count": len(target_pairs),
                "trade_date": session,
                "ts_codes_sha256": stats["target_scope_codes_sha256"],
            }
        )

    boundary_proofs = _validate_transition_boundaries(
        trade_dates=trade_dates,
        filtered_codes_by_date=filtered_codes_by_date,
        transitions_by_code=transitions_by_code,
    )
    boundary_root = _canonical_sha256(boundary_proofs)
    daily_descriptor, transition_descriptor = _authority_descriptors(
        daily,
        transition,
    )
    trade_dates_sha256 = _canonical_sha256(trade_dates)
    per_date_root = _canonical_sha256(per_date)
    normalized_root = _canonical_sha256(normalized_authority_rows)
    target_root = _canonical_sha256(target_identity_rows)
    overlap_root = _canonical_sha256(overlap_authority_rows)
    producer = _producer_binding()
    source_binding = {
        "audited_daily_authority": daily_descriptor,
        "normalized_daily_basic_row_authority_root_sha256": normalized_root,
        "producer_root_sha256": producer["root_sha256"],
        "security_code_transition_authority": transition_descriptor,
        "trade_dates_sha256": trade_dates_sha256,
        "transition_boundary_authority_root_sha256": boundary_root,
    }
    unsigned = {
        "all_supported_segments_compared_before_scope_filter": True,
        "arbitrary_row_drops_permitted": False,
        "audited_daily_authority": {
            **daily_descriptor,
            "daily_identity_root_sha256": _canonical_sha256(daily_identity_rows),
        },
        "authority_scope": "DEVELOPMENT_DAILY_BASIC_EXACT_SET_INPUT_ONLY",
        "authority_status": _AUTHORITY_STATUS,
        "embargo_consumed": False,
        "exact_set_verified": True,
        "factor_v3_development_materialization_input_eligible": True,
        "factor_v3_target_identity_root_sha256": target_root,
        "factor_v3_target_scope": {
            "excluded_segments": ["SSE_STAR", "BSE"],
            "included_segments": ["SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"],
            "policy_id": POLICY_ID,
            "target_identity_row_count": target_total,
        },
        "final_oos_consumed": False,
        "formal_factor_v3_materialization_performed": False,
        "normalized_daily_basic_row_authority_root_sha256": normalized_root,
        "per_date_statistics": per_date,
        "per_date_statistics_sha256": per_date_root,
        "producer_binding": producer,
        "publication_capability_sha256": capability_sha256,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "raw_source_rows_bound": True,
        "row_authority_status": _ROW_AUTHORITY_STATUS,
        "rows_published": False,
        "schema": RECEIPT_SCHEMA,
        "security_code_transition_authority": transition_descriptor,
        "silent_row_drops_permitted": False,
        "source_binding_root_sha256": _canonical_sha256(source_binding),
        "source_missingness": {
            "extra_daily_basic_code_count": 0,
            "missing_authoritative_daily_code_count": 0,
            "status": "NONE_AFTER_AUTHORIZED_TRANSITION_FILTER",
            "unproven_source_missingness_count": 0,
        },
        "source_ts_code_exact_set_verified_after_transition_filter": True,
        "trade_date_count": len(trade_dates),
        "trade_dates": trade_dates,
        "trade_dates_sha256": trade_dates_sha256,
        "transition_boundary_authority_root_sha256": boundary_root,
        "transition_boundary_count": len(boundary_proofs),
        "transition_overlap_authority_root_sha256": overlap_root,
        "transition_resolved_identity_exact_set_verified": True,
    }
    return {**unsigned, "authority_root_sha256": _canonical_sha256(unsigned)}


def _load_audited_daily_authority(
    *,
    audited_universe_sqlite_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    expected_temporal_contract_sha256: str,
    expected_temporal_role: str,
) -> AuditedDailyAuthority:
    database_path = Path(audited_universe_sqlite_path)
    with AuditedPointInTimeUniverse.from_file(
        str(database_path),
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_temporal_contract_sha256,
        expected_temporal_role=expected_temporal_role,
    ) as universe:
        database_path = universe.database_path
        manifest = universe.manifest
        refs = manifest["market_generations"]["refs"]
        by_date = {str(ref["trade_date"]): ref for ref in refs}
        if len(by_date) != len(refs):
            raise ValueError("audited market generation dates rejected")
        connection = universe._require_open()
        grouped: list[AuthoritativeDailyPartition] = []
        cursor = connection.execute(
            """
            SELECT trade_date, generation_id, ts_code
            FROM market_session_generation_rows_daily
            ORDER BY trade_date, ts_code
            """
        )
        current_date: str | None = None
        current_generation: str | None = None
        codes: list[str] = []

        def flush() -> None:
            nonlocal current_date, current_generation, codes
            if current_date is None or current_generation is None:
                return
            ref = by_date.get(current_date)
            if ref is None or str(ref["generation_id"]) != current_generation:
                raise ValueError("audited daily row generation binding rejected")
            grouped.append(
                AuthoritativeDailyPartition(
                    trade_date=current_date,
                    generation_id=current_generation,
                    generation_manifest_sha256=str(ref["manifest_sha256"]),
                    generation_lineage_sha256=str(ref["lineage_sha256"]),
                    vintage=str(ref["vintage"]),
                    ts_codes=tuple(codes),
                )
            )
            current_date = None
            current_generation = None
            codes = []

        for row in cursor:
            session = str(row["trade_date"])
            generation_id = str(row["generation_id"])
            if current_date is not None and session != current_date:
                flush()
            if current_date is None:
                current_date = session
                current_generation = generation_id
            elif generation_id != current_generation:
                raise ValueError("audited daily partition has multiple generations")
            codes.append(str(row["ts_code"]))
        flush()
        if [item.trade_date for item in grouped] != [str(ref["trade_date"]) for ref in refs]:
            raise ValueError("audited daily partitions do not cover all market generation refs")
        manifest_raw = raw_authority._read_safe_file(
            database_path.parent / "manifest.json",
            label="audited daily manifest",
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        result = AuditedDailyAuthority(
            manifest_file_sha256=_sha256(manifest_raw),
            manifest_sha256=str(manifest["manifest_sha256"]),
            bundle_sha256=str(manifest["bundle_sha256"]),
            artifact_root_sha256=str(manifest["artifact_root_sha256"]),
            sqlite_sha256=str(manifest["sqlite"]["sha256"]),
            coverage_audit_sha256=str(manifest["coverage_audit_sha256"]),
            temporal_contract_sha256=str(manifest["temporal_binding"]["contract_sha256"]),
            temporal_role=str(manifest["temporal_binding"]["role"]),
            daily_table_rows=int(
                manifest["tables"]["market_session_generation_rows_daily"]["rows"]
            ),
            daily_table_sha256=str(
                manifest["tables"]["market_session_generation_rows_daily"]["sha256"]
            ),
            market_generation_count=int(manifest["market_generations"]["count"]),
            market_generation_root_sha256=str(manifest["market_generations"]["root_sha256"]),
            partitions=tuple(grouped),
        )
    _validate_audited_daily_authority(result)
    return result


def _load_daily_basic_partition(
    *,
    points_output_root: str | Path,
    collection_ref: Mapping[str, str],
) -> DailyBasicPartition:
    relative_path = collection_ref["collection_set_relative_path"]
    if type(relative_path) is str and relative_path.startswith("daily_basic_collection_sets/"):
        payload = daily_basic_collection._load_and_verify(
            output_root=points_output_root,
            collection_set_relative_path=relative_path,
            expected_collection_set_sha256=collection_ref["collection_set_sha256"],
        )
    else:
        payload = points_collection._verify_and_load_collection_set(
            output_root=points_output_root,
            collection_set_relative_path=relative_path,
            expected_collection_set_sha256=collection_ref["collection_set_sha256"],
        )
    if payload.get("trade_date") != collection_ref["trade_date"]:
        raise ValueError("daily_basic collection trade_date rejected")
    attempts = payload.get("attempts")
    matches = [
        item for item in attempts if type(item) is dict and item.get("api_name") == "daily_basic"
    ]
    if len(matches) != 1:
        raise ValueError("daily_basic collection attempt binding rejected")
    attempt = matches[0]
    root = raw_authority._safe_existing_directory(
        Path(points_output_root),
        "daily_basic collection root",
    )
    raw = raw_authority._read_safe_file(
        root / Path(*str(attempt["raw_relative_path"]).split("/")),
        label="daily_basic raw response",
        max_bytes=_MAX_RAW_BODY_BYTES,
    )
    preview = normalize_jiaoch_points_response_preview(
        response_body=raw,
        expected_api_name="daily_basic",
        expected_trade_date=collection_ref["trade_date"].replace("-", ""),
        expected_raw_sha256=str(attempt["raw_sha256"]),
    )
    if (
        preview.collection_set_verified is not False
        or preview.source_authority_verified is not False
        or preview.row_authority_status != "NOT_GRANTED"
        or preview.formal_materialization_eligible is not False
        or preview.embargo_consumed is not False
        or preview.final_oos_consumed is not False
        or preview.production_profile_registered is not False
        or preview.production_recommendation_eligible is not False
    ):
        raise ValueError("daily_basic normalization preview semantics rejected")
    normalized_rows = tuple(
        replace(row, trade_date=collection_ref["trade_date"]) for row in preview.rows
    )
    result = DailyBasicPartition(
        trade_date=collection_ref["trade_date"],
        collection_set_relative_path=collection_ref["collection_set_relative_path"],
        collection_set_sha256=collection_ref["collection_set_sha256"],
        attempt_relative_path=str(attempt["attempt_relative_path"]),
        attempt_sha256=str(attempt["attempt_sha256"]),
        raw_relative_path=str(attempt["raw_relative_path"]),
        raw_sha256=str(attempt["raw_sha256"]),
        source_normalization_rows_sha256=preview.canonical_rows_sha256,
        canonical_rows_sha256=_canonical_sha256([asdict(row) for row in normalized_rows]),
        normalization_receipt_sha256=preview.preview_receipt_sha256,
        rows=normalized_rows,
    )
    _validate_daily_basic_partition(result, collection_ref=collection_ref)
    return result


def _load_transition_authority(
    *,
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
) -> TransitionAuthority:
    expected = _sha256_text(
        expected_security_code_transition_contract_sha256,
        label="expected transition contract sha256",
    )
    loaded = code_transition.load_security_code_transition_evidence(
        security_code_transition_evidence_root,
        expected_contract_sha256=expected,
    )
    if loaded.get("contract_sha256") != expected:
        raise ValueError("security-code transition contract binding rejected")
    contract = code_transition._validated_contract(loaded.get("contract"))
    if not hmac.compare_digest(
        code_transition.canonical_sha256(contract),
        expected,
    ):
        raise ValueError("security-code transition contract root rejected")
    receipt_identity = {
        "schema_version": loaded.get("schema_version"),
        "contract_sha256": loaded.get("contract_sha256"),
        "evidence_receipts": loaded.get("evidence_receipts"),
    }
    if not hmac.compare_digest(
        code_transition.canonical_sha256(receipt_identity),
        str(loaded.get("receipt_sha256")),
    ):
        raise ValueError("security-code transition evidence receipt rejected")
    by_code: dict[str, Mapping[str, Any]] = {}
    for item in contract["transitions"]:
        descriptor = {key: item[key] for key in _TRANSITION_FIELDS}
        for code in (
            descriptor["predecessor_ts_code"],
            descriptor["successor_ts_code"],
        ):
            if code in by_code:
                raise ValueError("security-code transition identity is ambiguous")
            by_code[code] = descriptor
    result = TransitionAuthority(
        contract_sha256=expected,
        evidence_receipt_sha256=str(loaded["receipt_sha256"]),
        evidence_receipts_sha256=_canonical_sha256(loaded["evidence_receipts"]),
        transition_count=len(contract["transitions"]),
        transitions_by_code=by_code,
    )
    _validate_transition_authority(result)
    return result


def _validate_receipt_payload(payload: Any) -> dict[str, Any]:
    if (
        type(payload) is not dict
        or set(payload) != _RECEIPT_FIELDS
        or payload.get("schema") != RECEIPT_SCHEMA
        or payload.get("authority_status") != _AUTHORITY_STATUS
        or payload.get("authority_scope") != "DEVELOPMENT_DAILY_BASIC_EXACT_SET_INPUT_ONLY"
        or payload.get("row_authority_status") != _ROW_AUTHORITY_STATUS
        or payload.get("exact_set_verified") is not True
        or payload.get("raw_source_rows_bound") is not True
        or payload.get("source_ts_code_exact_set_verified_after_transition_filter") is not True
        or payload.get("transition_resolved_identity_exact_set_verified") is not True
        or payload.get("all_supported_segments_compared_before_scope_filter") is not True
        or payload.get("factor_v3_development_materialization_input_eligible") is not True
        or payload.get("formal_factor_v3_materialization_performed") is not False
        or payload.get("arbitrary_row_drops_permitted") is not False
        or payload.get("silent_row_drops_permitted") is not False
        or payload.get("rows_published") is not False
        or payload.get("embargo_consumed") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("production_profile_registered") is not False
        or payload.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("daily_basic exact-set receipt descriptor rejected")
    authority_root = _sha256_text(
        payload.get("authority_root_sha256"),
        label="daily_basic exact-set authority root",
    )
    unsigned = {key: value for key, value in payload.items() if key != "authority_root_sha256"}
    if not hmac.compare_digest(_canonical_sha256(unsigned), authority_root):
        raise ValueError("daily_basic exact-set authority root rejected")
    _sha256_text(
        payload.get("publication_capability_sha256"),
        label="daily_basic exact-set publication capability sha256",
    )
    dates = payload.get("trade_dates")
    count = payload.get("trade_date_count")
    if (
        type(dates) is not list
        or not dates
        or type(count) is not int
        or count != len(dates)
        or dates != sorted(set(dates))
    ):
        raise ValueError("daily_basic exact-set receipt trade dates rejected")
    for session in dates:
        _trade_date(session)
    if payload.get("trade_dates_sha256") != _canonical_sha256(dates):
        raise ValueError("daily_basic exact-set trade date root rejected")
    stats = payload.get("per_date_statistics")
    if type(stats) is not list or len(stats) != count:
        raise ValueError("daily_basic exact-set per-date statistics rejected")
    if payload.get("per_date_statistics_sha256") != _canonical_sha256(stats):
        raise ValueError("daily_basic exact-set per-date root rejected")
    for session, item in zip(dates, stats, strict=True):
        if (
            type(item) is not dict
            or set(item) != _PER_DATE_FIELDS
            or item.get("trade_date") != session
            or item.get("source_ts_code_exact_set_verified_after_transition_filter") is not True
            or item.get("transition_resolved_identity_exact_set_verified") is not True
            or item.get("missing_authoritative_daily_code_count") != 0
            or item.get("extra_daily_basic_code_count") != 0
        ):
            raise ValueError("daily_basic exact-set per-date descriptor rejected")
    if payload.get("source_missingness") != {
        "extra_daily_basic_code_count": 0,
        "missing_authoritative_daily_code_count": 0,
        "status": "NONE_AFTER_AUTHORIZED_TRANSITION_FILTER",
        "unproven_source_missingness_count": 0,
    }:
        raise ValueError("daily_basic exact-set source missingness rejected")
    return payload


def _receipt_path(
    *,
    root: Path,
    relative_path: str,
    expected_sha256: str,
) -> Path:
    digest = _sha256_text(
        expected_sha256,
        label="daily_basic exact-set receipt sha256",
    )
    if type(relative_path) is not str:
        raise ValueError("daily_basic exact-set receipt path rejected")
    match = _RECEIPT_PATH_PATTERN.fullmatch(relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("daily_basic exact-set receipt path rejected")
    return raw_authority._safe_existing_file(
        root / Path(*relative_path.split("/")),
        "daily_basic exact-set receipt",
    )


def _write_receipt_candidate_create_only(path: Path, raw: bytes) -> bool:
    parent = raw_authority._safe_existing_directory(
        path.parent,
        "daily_basic exact-set candidate parent",
    )
    if parent / path.name != path or type(raw) is not bytes:
        raise ValueError("daily_basic exact-set candidate input rejected")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raw_authority._safe_existing_file(
            path,
            "daily_basic exact-set candidate",
        )
        raise ValueError("daily_basic exact-set candidate content-address conflict") from None
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    with handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    raw_authority.fsync_directory(parent)
    return True


def publish_daily_basic_exact_set_coverage(
    *,
    audited_universe_sqlite_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    expected_temporal_contract_sha256: str,
    expected_temporal_role: str,
    points_output_root: str | Path,
    collection_set_refs: Sequence[Mapping[str, Any]],
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish a development-only exact-set receipt without network or secrets."""

    publication_capability = str(uuid.uuid4())
    publication_capability_sha256 = _sha256(publication_capability.encode("utf-8"))
    receipt = _derive_receipt(
        audited_universe_sqlite_path=audited_universe_sqlite_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_temporal_contract_sha256,
        expected_temporal_role=expected_temporal_role,
        points_output_root=points_output_root,
        collection_set_refs=collection_set_refs,
        security_code_transition_evidence_root=(security_code_transition_evidence_root),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        publication_capability_sha256=publication_capability_sha256,
    )
    _validate_receipt_payload(receipt)
    raw = _canonical_json(receipt)
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise ValueError("daily_basic exact-set receipt size rejected")
    digest = _sha256(raw)
    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "daily_basic exact-set output root",
    )
    directory = raw_authority._content_addressed_directory(
        root,
        "daily_basic_exact_set_coverage_candidates",
        digest,
    )
    path = directory / f"{digest}.json"
    relative_path = f"daily_basic_exact_set_coverage_candidates/sha256/{digest[:2]}/{digest}.json"
    created = _write_receipt_candidate_create_only(path, raw)
    verify_daily_basic_exact_set_coverage(
        audited_universe_sqlite_path=audited_universe_sqlite_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_temporal_contract_sha256,
        expected_temporal_role=expected_temporal_role,
        points_output_root=points_output_root,
        security_code_transition_evidence_root=(security_code_transition_evidence_root),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        output_root=output_root,
        receipt_relative_path=relative_path,
        expected_receipt_sha256=digest,
        publication_capability=publication_capability,
    )
    return {
        "authority_root_sha256": receipt["authority_root_sha256"],
        "publication_capability": publication_capability,
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "receipt_created": created,
        "receipt_relative_path": relative_path,
        "receipt_sha256": digest,
        "schema": "daily-basic-exact-set-publication/v1",
    }


def verify_daily_basic_exact_set_coverage(
    *,
    audited_universe_sqlite_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    expected_temporal_contract_sha256: str,
    expected_temporal_role: str,
    points_output_root: str | Path,
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    output_root: str | Path,
    receipt_relative_path: str,
    expected_receipt_sha256: str,
    publication_capability: str,
) -> dict[str, Any]:
    """Replay every bound input and verify one content-addressed receipt offline."""

    capability = _publication_capability(publication_capability)
    capability_sha256 = _sha256(capability.encode("utf-8"))
    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "daily_basic exact-set output root",
    )
    path = _receipt_path(
        root=root,
        relative_path=receipt_relative_path,
        expected_sha256=expected_receipt_sha256,
    )
    raw = raw_authority._read_safe_file(
        path,
        label="daily_basic exact-set receipt",
        max_bytes=_MAX_RECEIPT_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_receipt_sha256):
        raise ValueError("daily_basic exact-set receipt content address rejected")
    payload = _strict_json_loads(raw)
    if not hmac.compare_digest(raw, _canonical_json(payload)):
        raise ValueError("daily_basic exact-set receipt canonical form rejected")
    _validate_receipt_payload(payload)
    if not hmac.compare_digest(
        payload["publication_capability_sha256"],
        capability_sha256,
    ):
        raise ValueError("daily_basic exact-set publication capability rejected")
    refs = [
        {
            "collection_set_relative_path": item["collection_set_relative_path"],
            "collection_set_sha256": item["collection_set_sha256"],
            "trade_date": item["trade_date"],
        }
        for item in payload["per_date_statistics"]
    ]
    replay = _derive_receipt(
        audited_universe_sqlite_path=audited_universe_sqlite_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_temporal_contract_sha256,
        expected_temporal_role=expected_temporal_role,
        points_output_root=points_output_root,
        collection_set_refs=refs,
        security_code_transition_evidence_root=(security_code_transition_evidence_root),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        publication_capability_sha256=capability_sha256,
    )
    if not hmac.compare_digest(_canonical_json(replay), _canonical_json(payload)):
        raise ValueError("daily_basic exact-set receipt replay descriptor mismatch")
    return {
        **payload,
        "receipt_sha256": expected_receipt_sha256,
        "verified": True,
    }
