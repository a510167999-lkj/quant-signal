"""Capability-free, content-addressed exact-set authority for 733 Factor V3 sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app import factor_v3_feature_history_runner as history_runner
from app import factor_v3_feature_history_frozen_source_attestation as frozen_attestation
from app import jiaoch_daily_basic_exact_set_authority as legacy
from app import jiaoch_points_raw_authority as raw_authority


__all__ = (
    "publish_factor_v3_daily_basic_733_exact_set_coverage",
    "verify_factor_v3_daily_basic_733_exact_set_coverage",
)


RECEIPT_SCHEMA = "factor-v3-daily-basic-733-exact-set-receipt/v2"
ATTESTATION_SCHEMA = "factor-v3-daily-basic-733-postverification-attestation/v1"
PUBLICATION_SCHEMA = "factor-v3-daily-basic-733-exact-set-publication/v2"
PUBLICATION_MANIFEST_SCHEMA = "factor-v3-daily-basic-733-publication-manifest/v1"
PRODUCER_VERSION = "app.factor_v3_daily_basic_733_exact_set_authority/1"
_AUTHORITY_STATUS = "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET"
_AUTHORITY_SCOPE = "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY"
_ROW_AUTHORITY_STATUS = "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY"
_FROZEN_DEVELOPMENT_SLICE_ROLE = "development_4"
_AUDITED_DEVELOPMENT_TEMPORAL_ROLE = "development"
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_KINDS = {
    "receipt": "factor_v3_daily_basic_733_receipts",
    "attestation": "factor_v3_daily_basic_733_attestations",
    "publication": "factor_v3_daily_basic_733_publications",
}
_PRODUCER_FILES = (
    "audited_pit_factor_v3_feature_history_authority.py",
    "durable_io.py",
    "factor_v3_daily_basic_733_exact_set_authority.py",
    "factor_v3_feature_history_frozen_source_attestation.py",
    "factor_v3_feature_history_runner.py",
    "jiaoch_daily_basic_collection_set.py",
    "jiaoch_daily_basic_exact_set_authority.py",
    "jiaoch_points_response_normalization.py",
    "jiaoch_points_raw_authority.py",
    "research_pit_store.py",
    "research_scope.py",
    "research_security_code_transition.py",
)
_PUBLICATION_FIELDS = frozenset(
    {
        "attestation_relative_path",
        "attestation_sha256",
        "publication_relative_path",
        "publication_sha256",
        "receipt_relative_path",
        "receipt_sha256",
        "schema",
    }
)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("factor-v3 daily-basic 733 canonical JSON rejected") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 daily-basic 733 {label} rejected")
    return value


def _producer_binding() -> dict[str, Any]:
    root = raw_authority._safe_existing_directory(
        Path(__file__).parent,
        "factor-v3 daily-basic 733 producer root",
    )
    entries = []
    for filename in _PRODUCER_FILES:
        raw = raw_authority._read_safe_file(
            root / filename,
            label="factor-v3 daily-basic 733 producer source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        entries.append({"path": f"app/{filename}", "sha256": _sha256(raw)})
    identity = {
        "entries": entries,
        "producer_version": PRODUCER_VERSION,
        "schema": "factor-v3-daily-basic-733-producer/v1",
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


def _authority_descriptor(value: legacy.AuditedDailyAuthority) -> dict[str, Any]:
    return {
        "artifact_root_sha256": value.artifact_root_sha256,
        "bundle_sha256": value.bundle_sha256,
        "coverage_audit_sha256": value.coverage_audit_sha256,
        "daily_table_rows": value.daily_table_rows,
        "daily_table_sha256": value.daily_table_sha256,
        "manifest_file_sha256": value.manifest_file_sha256,
        "manifest_sha256": value.manifest_sha256,
        "market_generation_count": value.market_generation_count,
        "market_generation_root_sha256": value.market_generation_root_sha256,
        "sqlite_sha256": value.sqlite_sha256,
        "temporal_contract_sha256": value.temporal_contract_sha256,
        "temporal_role": value.temporal_role,
    }


def _partition_refs(
    partitions: Sequence[legacy.AuthoritativeDailyPartition],
) -> list[dict[str, Any]]:
    return [
        {
            "generation_id": item.generation_id,
            "lineage_sha256": item.generation_lineage_sha256,
            "manifest_sha256": item.generation_manifest_sha256,
            "trade_date": item.trade_date,
            "vintage": item.vintage,
        }
        for item in partitions
    ]


def _combine_authorities(
    prewindow: legacy.AuditedDailyAuthority,
    development: legacy.AuditedDailyAuthority,
) -> tuple[legacy.AuditedDailyAuthority, dict[str, Any]]:
    pre = legacy._validate_audited_daily_authority(prewindow)
    dev = legacy._validate_audited_daily_authority(development)
    if len(pre) != 250 or len(dev) != 483:
        raise ValueError("factor-v3 daily-basic requires verified 250+483 source authorities")
    partitions = (*pre, *dev)
    dates = [item.trade_date for item in partitions]
    if (
        len(dates) != 733
        or dates != sorted(dates)
        or len(set(dates)) != 733
        or pre[-1].trade_date >= dev[0].trade_date
    ):
        raise ValueError("factor-v3 daily-basic requires exact ordered 733 source sessions")
    pre_descriptor = _authority_descriptor(prewindow)
    dev_descriptor = _authority_descriptor(development)
    source_identity = {
        "development_authority": dev_descriptor,
        "prewindow_feature_history_authority": pre_descriptor,
        "schema": "factor-v3-daily-basic-733-source-authorities/v1",
        "session_count": 733,
        "sessions_sha256": _canonical_sha256(dates),
        "temporal_role_binding": {
            "audited_artifact_role": _AUDITED_DEVELOPMENT_TEMPORAL_ROLE,
            "frozen_development_slice_role": _FROZEN_DEVELOPMENT_SLICE_ROLE,
            "partition_vintage_binding": "published_terminal_at",
        },
    }
    refs = _partition_refs(partitions)
    rows = [
        {"trade_date": item.trade_date, "ts_codes": list(item.ts_codes)}
        for item in partitions
    ]
    combined = legacy.AuditedDailyAuthority(
        manifest_file_sha256=_canonical_sha256(
            [prewindow.manifest_file_sha256, development.manifest_file_sha256]
        ),
        manifest_sha256=_canonical_sha256(
            [prewindow.manifest_sha256, development.manifest_sha256]
        ),
        bundle_sha256=_canonical_sha256(
            [prewindow.bundle_sha256, development.bundle_sha256]
        ),
        artifact_root_sha256=_canonical_sha256(source_identity),
        sqlite_sha256=_canonical_sha256(
            [prewindow.sqlite_sha256, development.sqlite_sha256]
        ),
        coverage_audit_sha256=_canonical_sha256(
            [prewindow.coverage_audit_sha256, development.coverage_audit_sha256]
        ),
        temporal_contract_sha256=_canonical_sha256(
            [prewindow.temporal_contract_sha256, development.temporal_contract_sha256]
        ),
        temporal_role="development",
        daily_table_rows=sum(len(item.ts_codes) for item in partitions),
        daily_table_sha256=_canonical_sha256(rows),
        market_generation_count=733,
        market_generation_root_sha256=_canonical_sha256(refs),
        partitions=partitions,
    )
    legacy._validate_audited_daily_authority(combined)
    return combined, {
        **source_identity,
        "root_sha256": _canonical_sha256(source_identity),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_feature_history_prewindow_authority(
    *,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    feature_history_frozen_source_attestation_path: str | Path,
    expected_feature_history_frozen_source_attestation_sha256: str,
    feature_history_frozen_source_root: str | Path,
    expected_feature_history_frozen_source_commit: str,
) -> legacy.AuditedDailyAuthority:
    verified = frozen_attestation.verify_factor_v3_feature_history_frozen_source_attestation(
        attestation_path=feature_history_frozen_source_attestation_path,
        expected_attestation_sha256=(
            expected_feature_history_frozen_source_attestation_sha256
        ),
        frozen_source_root=feature_history_frozen_source_root,
        expected_frozen_source_commit=expected_feature_history_frozen_source_commit,
        feature_history_run_spec_path=feature_history_run_spec_path,
        feature_history_run_root=feature_history_run_root,
    )
    if (
        type(verified) is not dict
        or verified.get("verified") is not True
        or verified.get("session_count") != 250
    ):
        raise ValueError("factor-v3 daily-basic feature-history authority rejected")
    spec = history_runner.load_factor_v3_feature_history_run_spec(
        feature_history_run_spec_path
    )
    segments = history_runner._validated_segments(spec["collection_plan"])
    sessions = [session for segment in segments for session in segment["sessions"]]
    if len(sessions) != 250:
        raise ValueError("factor-v3 daily-basic feature-history prewindow rejected")
    paths = history_runner._run_paths(feature_history_run_root, create=False)
    state = history_runner._validated_state(
        history_runner._read_json_file(
            paths["state"],
            label="run state",
            max_bytes=history_runner._MAX_STATE_BYTES,
        ),
        run_spec_sha256=spec["run_spec_sha256"],
    )
    receipt = state.get("receipt")
    if type(receipt) is not dict or receipt.get("verified") is not True:
        raise ValueError("factor-v3 daily-basic feature-history receipt rejected")
    publication = history_runner._validated_publication(state["collection_publication"])
    manifest = history_authority._read_collection_manifest(
        output_root=paths["publication_root"],
        publication=publication,
    )
    refs = manifest.get("session_authority_refs")
    if type(refs) is not list or len(refs) != 250:
        raise ValueError("factor-v3 daily-basic feature-history refs rejected")
    ref_by_date = {str(item["trade_date"]): item for item in refs}
    if list(ref_by_date) != sessions:
        raise ValueError("factor-v3 daily-basic feature-history refs rejected")
    database = paths["store"] / "metadata.sqlite3"
    if (
        database.is_symlink()
        or not database.is_file()
        or any(Path(f"{database}{suffix}").exists() for suffix in ("-wal", "-shm"))
        or _file_sha256(database) != receipt.get("pit_store_database_sha256")
    ):
        raise ValueError("factor-v3 daily-basic feature-history database rejected")
    connection = sqlite3.connect(
        f"{database.resolve(strict=True).as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        generations = {
            str(row[0]): {
                "generation_id": str(row[1]),
                "published_at": str(row[2]),
                "source_vintage": str(row[3]),
                "manifest_sha256": str(row[4]),
                "lineage_sha256": str(row[5]),
            }
            for row in connection.execute(
                """
                SELECT trade_date, generation_id, terminal_at, vintage,
                       manifest_sha256, lineage_sha256
                FROM market_session_generations
                WHERE status = 'published'
                ORDER BY trade_date
                """
            )
        }
        codes_by_date: dict[str, list[str]] = {session: [] for session in sessions}
        for row in connection.execute(
            """
            SELECT trade_date, generation_id, ts_code
            FROM market_session_generation_rows_daily
            ORDER BY trade_date, ts_code
            """
        ):
            session, generation_id, code = map(str, row)
            if (
                session not in codes_by_date
                or generations.get(session, {}).get("generation_id") != generation_id
            ):
                raise ValueError("factor-v3 daily-basic feature-history rows rejected")
            codes_by_date[session].append(code)
    finally:
        connection.close()
    partitions = []
    for session in sessions:
        generation = generations.get(session)
        ref = ref_by_date.get(session)
        codes = codes_by_date.get(session)
        if (
            generation is None
            or ref is None
            or not codes
            or codes != sorted(set(codes))
            or generation["source_vintage"]
            not in {"live_forward", "historical_backfill"}
            or generation["generation_id"] != ref.get("market_generation_id")
            or generation["manifest_sha256"]
            != ref.get("market_generation_manifest_sha256")
            or generation["lineage_sha256"]
            != ref.get("market_generation_lineage_sha256")
        ):
            raise ValueError("factor-v3 daily-basic feature-history partition rejected")
        partitions.append(
            legacy.AuthoritativeDailyPartition(
                trade_date=session,
                generation_id=generation["generation_id"],
                generation_manifest_sha256=generation["manifest_sha256"],
                generation_lineage_sha256=generation["lineage_sha256"],
                vintage=generation["published_at"],
                ts_codes=tuple(codes),
            )
        )
    partition_tuple = tuple(partitions)
    refs_for_root = _partition_refs(partition_tuple)
    return legacy.AuditedDailyAuthority(
        manifest_file_sha256=publication["authority_manifest_sha256"],
        manifest_sha256=receipt["collection_publication_manifest_sha256"],
        bundle_sha256=receipt["snapshot_index_sha256"],
        artifact_root_sha256=receipt["source_authority_root_sha256"],
        sqlite_sha256=receipt["pit_store_database_sha256"],
        coverage_audit_sha256=receipt["session_authority_refs_sha256"],
        temporal_contract_sha256=spec["temporal_partition_contract"]["contract_sha256"],
        temporal_role="development",
        daily_table_rows=sum(len(item.ts_codes) for item in partition_tuple),
        daily_table_sha256=_canonical_sha256(
            [
                {"trade_date": item.trade_date, "ts_codes": list(item.ts_codes)}
                for item in partition_tuple
            ]
        ),
        market_generation_count=250,
        market_generation_root_sha256=_canonical_sha256(refs_for_root),
        partitions=partition_tuple,
    )


def _load_development_authority(
    *,
    audited_development_universe_sqlite_path: str | Path,
    expected_development_coverage_audit_sha256: str,
    expected_development_artifact_root_sha256: str,
    expected_development_temporal_contract_sha256: str,
    expected_development_temporal_role: str,
) -> legacy.AuditedDailyAuthority:
    if expected_development_temporal_role != _FROZEN_DEVELOPMENT_SLICE_ROLE:
        raise ValueError(
            "factor-v3 daily-basic development slice temporal role rejected"
        )
    database_path = Path(audited_development_universe_sqlite_path)
    with legacy.AuditedPointInTimeUniverse.from_file(
        str(database_path),
        expected_coverage_audit_sha256=expected_development_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_development_artifact_root_sha256,
        expected_temporal_contract_sha256=expected_development_temporal_contract_sha256,
        expected_temporal_role=_AUDITED_DEVELOPMENT_TEMPORAL_ROLE,
    ) as universe:
        database_path = universe.database_path
        manifest = universe.manifest
        refs = manifest["market_generations"]["refs"]
        ref_by_date = {str(ref["trade_date"]): ref for ref in refs}
        if len(ref_by_date) != len(refs):
            raise ValueError("factor-v3 daily-basic development refs rejected")
        connection = universe._require_open()
        generations = {
            str(row["trade_date"]): {
                "generation_id": str(row["generation_id"]),
                "published_at": str(row["terminal_at"]),
                "source_vintage": str(row["vintage"]),
                "manifest_sha256": str(row["manifest_sha256"]),
                "lineage_sha256": str(row["lineage_sha256"]),
            }
            for row in connection.execute(
                """
                SELECT trade_date, generation_id, terminal_at, vintage,
                       manifest_sha256, lineage_sha256
                FROM market_session_generations
                WHERE status = 'published'
                ORDER BY trade_date
                """
            )
        }
        codes_by_date: dict[str, list[str]] = {
            str(ref["trade_date"]): [] for ref in refs
        }
        for row in connection.execute(
            """
            SELECT trade_date, generation_id, ts_code
            FROM market_session_generation_rows_daily
            ORDER BY trade_date, ts_code
            """
        ):
            session = str(row["trade_date"])
            generation_id = str(row["generation_id"])
            code = str(row["ts_code"])
            if (
                session not in codes_by_date
                or generations.get(session, {}).get("generation_id") != generation_id
            ):
                raise ValueError("factor-v3 daily-basic development rows rejected")
            codes_by_date[session].append(code)
        partitions = []
        for ref in refs:
            session = str(ref["trade_date"])
            generation = generations.get(session)
            codes = codes_by_date.get(session)
            if (
                generation is None
                or not codes
                or codes != sorted(set(codes))
                or generation["source_vintage"]
                not in {"live_forward", "historical_backfill"}
                or generation["generation_id"] != str(ref["generation_id"])
                or generation["manifest_sha256"] != str(ref["manifest_sha256"])
                or generation["lineage_sha256"] != str(ref["lineage_sha256"])
                or generation["source_vintage"] != str(ref["vintage"])
            ):
                raise ValueError(
                    "factor-v3 daily-basic development partition rejected"
                )
            partitions.append(
                legacy.AuthoritativeDailyPartition(
                    trade_date=session,
                    generation_id=generation["generation_id"],
                    generation_manifest_sha256=generation["manifest_sha256"],
                    generation_lineage_sha256=generation["lineage_sha256"],
                    vintage=generation["published_at"],
                    ts_codes=tuple(codes),
                )
            )
        partition_tuple = tuple(partitions)
        manifest_raw = raw_authority._read_safe_file(
            database_path.parent / "manifest.json",
            label="factor-v3 daily-basic development manifest",
            max_bytes=legacy._MAX_MANIFEST_BYTES,
        )
        authority = legacy.AuditedDailyAuthority(
            manifest_file_sha256=_sha256(manifest_raw),
            manifest_sha256=str(manifest["manifest_sha256"]),
            bundle_sha256=str(manifest["bundle_sha256"]),
            artifact_root_sha256=str(manifest["artifact_root_sha256"]),
            sqlite_sha256=str(manifest["sqlite"]["sha256"]),
            coverage_audit_sha256=str(manifest["coverage_audit_sha256"]),
            temporal_contract_sha256=str(
                manifest["temporal_binding"]["contract_sha256"]
            ),
            temporal_role=str(manifest["temporal_binding"]["role"]),
            daily_table_rows=int(
                manifest["tables"]["market_session_generation_rows_daily"][
                    "rows"
                ]
            ),
            daily_table_sha256=str(
                manifest["tables"]["market_session_generation_rows_daily"][
                    "sha256"
                ]
            ),
            market_generation_count=int(manifest["market_generations"]["count"]),
            market_generation_root_sha256=_canonical_sha256(
                _partition_refs(partition_tuple)
            ),
            partitions=partition_tuple,
        )
    legacy._validate_audited_daily_authority(authority)
    if (
        authority.temporal_role != _AUDITED_DEVELOPMENT_TEMPORAL_ROLE
        or len(authority.partitions) != 483
    ):
        raise ValueError("factor-v3 daily-basic development authority requires exact 483 sessions")
    return authority


def _load_transition_authority(
    *,
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
) -> legacy.TransitionAuthority:
    return legacy._load_transition_authority(
        security_code_transition_evidence_root=security_code_transition_evidence_root,
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
    )


def _load_733_authority(**kwargs: Any) -> tuple[legacy.AuditedDailyAuthority, dict[str, Any]]:
    prewindow = _load_feature_history_prewindow_authority(
        feature_history_run_spec_path=kwargs["feature_history_run_spec_path"],
        feature_history_run_root=kwargs["feature_history_run_root"],
        feature_history_frozen_source_attestation_path=kwargs[
            "feature_history_frozen_source_attestation_path"
        ],
        expected_feature_history_frozen_source_attestation_sha256=kwargs[
            "expected_feature_history_frozen_source_attestation_sha256"
        ],
        feature_history_frozen_source_root=kwargs[
            "feature_history_frozen_source_root"
        ],
        expected_feature_history_frozen_source_commit=kwargs[
            "expected_feature_history_frozen_source_commit"
        ],
    )
    development = _load_development_authority(
        audited_development_universe_sqlite_path=kwargs[
            "audited_development_universe_sqlite_path"
        ],
        expected_development_coverage_audit_sha256=kwargs[
            "expected_development_coverage_audit_sha256"
        ],
        expected_development_artifact_root_sha256=kwargs[
            "expected_development_artifact_root_sha256"
        ],
        expected_development_temporal_contract_sha256=kwargs[
            "expected_development_temporal_contract_sha256"
        ],
        expected_development_temporal_role=kwargs[
            "expected_development_temporal_role"
        ],
    )
    combined, identity = _combine_authorities(prewindow, development)
    expected_root = kwargs.get("expected_source_authority_root_sha256")
    if expected_root is not None:
        expected = _sha256_text(
            expected_root,
            label="expected source authority root",
        )
        if not hmac.compare_digest(identity["root_sha256"], expected):
            raise ValueError("factor-v3 daily-basic source authority root rejected")
    return combined, identity


def _validated_refs(
    refs: Sequence[Mapping[str, Any]], sessions: Sequence[str]
) -> tuple[dict[str, str], ...]:
    if len(refs) != 733:
        raise ValueError("factor-v3 daily-basic requires exact 733 collection refs")
    for ref in refs:
        if (
            type(ref) is not dict
            or type(ref.get("collection_set_relative_path")) is not str
            or not ref["collection_set_relative_path"].startswith(
                "daily_basic_collection_sets/"
            )
        ):
            raise ValueError("daily_basic-only collection set required")
    return legacy._validated_collection_refs(refs, required_dates=sessions)


def _validate_v2_receipt(receipt: Any) -> dict[str, Any]:
    if (
        type(receipt) is not dict
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("authority_status") != _AUTHORITY_STATUS
        or receipt.get("authority_scope") != _AUTHORITY_SCOPE
        or receipt.get("row_authority_status") != _ROW_AUTHORITY_STATUS
        or receipt.get("trade_date_count") != 733
    ):
        raise ValueError("factor-v3 daily-basic 733 receipt rejected")
    authority_root = _sha256_text(
        receipt.get("authority_root_sha256"),
        label="authority root",
    )
    unsigned = {
        key: value for key, value in receipt.items() if key != "authority_root_sha256"
    }
    if not hmac.compare_digest(_canonical_sha256(unsigned), authority_root):
        raise ValueError("factor-v3 daily-basic 733 authority root rejected")
    legacy_view = {
        **receipt,
        "authority_scope": "DEVELOPMENT_DAILY_BASIC_EXACT_SET_INPUT_ONLY",
        "authority_status": legacy._AUTHORITY_STATUS,
        "row_authority_status": legacy._ROW_AUTHORITY_STATUS,
        "schema": legacy.RECEIPT_SCHEMA,
    }
    legacy_unsigned = {
        key: value
        for key, value in legacy_view.items()
        if key != "authority_root_sha256"
    }
    legacy_view["authority_root_sha256"] = _canonical_sha256(legacy_unsigned)
    legacy._validate_receipt_payload(legacy_view)
    return receipt


def _derive(
    *,
    source: legacy.AuditedDailyAuthority,
    source_identity: Mapping[str, Any],
    transition: legacy.TransitionAuthority,
    points_output_root: str | Path,
    collection_set_refs: Sequence[Mapping[str, Any]],
    publication_capability_sha256: str,
) -> dict[str, Any]:
    capability_sha256 = _sha256_text(
        publication_capability_sha256,
        label="publication nonce sha256",
    )
    sessions = [item.trade_date for item in source.partitions]
    refs = _validated_refs(collection_set_refs, sessions)
    partitions = legacy._validate_audited_daily_authority(source)
    transitions_by_code = legacy._validate_transition_authority(transition)
    producer = _producer_binding()

    per_date: list[dict[str, Any]] = []
    target_identity_rows: list[dict[str, Any]] = []
    normalized_authority_rows: list[dict[str, Any]] = []
    overlap_authority_rows: list[dict[str, Any]] = []
    daily_identity_rows: list[dict[str, Any]] = []
    filtered_codes_by_date: dict[str, tuple[str, ...]] = {}
    target_total = 0
    for daily_partition, collection_ref in zip(partitions, refs, strict=True):
        session = daily_partition.trade_date
        daily_basic = legacy._load_daily_basic_partition(
            points_output_root=points_output_root,
            collection_ref=collection_ref,
        )
        rows = legacy._validate_daily_basic_partition(
            daily_basic,
            collection_ref=collection_ref,
        )
        daily_raw_codes = daily_partition.ts_codes
        basic_raw_codes = tuple(row.ts_code for row in rows)
        overlap_proofs = legacy._daily_basic_overlap_proofs(
            rows,
            transitions_by_code=transitions_by_code,
        )
        daily_filtered, daily_identities, daily_excluded = (
            legacy._transition_filter_codes(
                daily_raw_codes,
                trade_date=session,
                transitions_by_code=transitions_by_code,
                source_label="authoritative daily",
            )
        )
        basic_filtered, basic_identities, basic_excluded = (
            legacy._transition_filter_codes(
                basic_raw_codes,
                trade_date=session,
                transitions_by_code=transitions_by_code,
                source_label="daily_basic",
            )
        )
        missing = sorted(set(daily_filtered) - set(basic_filtered))
        extra = sorted(set(basic_filtered) - set(daily_filtered))
        if missing or extra:
            raise ValueError(
                "daily and daily_basic source ts_code exact set failed after transition filter"
            )
        if daily_identities != basic_identities:
            raise ValueError(
                "daily and daily_basic resolved identity exact set failed"
            )
        target_pairs = [
            (code, identity)
            for code, identity in zip(
                basic_filtered,
                basic_identities,
                strict=True,
            )
            if legacy._market_segment(code)[0] in legacy._TARGET_SEGMENTS
        ]
        target_codes = [code for code, _identity in target_pairs]
        target_identities = [identity for _code, identity in target_pairs]
        target_total += len(target_pairs)
        filtered_codes_by_date[session] = daily_filtered
        overlap_root = _canonical_sha256(overlap_proofs)
        stats = {
            "authoritative_daily_filtered_codes_sha256": _canonical_sha256(
                list(daily_filtered)
            ),
            "authoritative_daily_generation_id": daily_partition.generation_id,
            "authoritative_daily_generation_lineage_sha256": (
                daily_partition.generation_lineage_sha256
            ),
            "authoritative_daily_generation_manifest_sha256": (
                daily_partition.generation_manifest_sha256
            ),
            "authoritative_daily_raw_codes_sha256": _canonical_sha256(
                list(daily_raw_codes)
            ),
            "authoritative_daily_raw_row_count": len(daily_raw_codes),
            "authoritative_daily_raw_segment_counts": legacy._segment_counts(
                daily_raw_codes
            ),
            "authoritative_daily_resolved_identities_sha256": _canonical_sha256(
                list(daily_identities)
            ),
            "authoritative_daily_transition_excluded_codes_sha256": (
                _canonical_sha256(list(daily_excluded))
            ),
            "authoritative_daily_transition_excluded_row_count": len(
                daily_excluded
            ),
            "authoritative_daily_vintage": daily_partition.vintage,
            "collection_set_relative_path": daily_basic.collection_set_relative_path,
            "collection_set_sha256": daily_basic.collection_set_sha256,
            "daily_basic_attempt_relative_path": daily_basic.attempt_relative_path,
            "daily_basic_attempt_sha256": daily_basic.attempt_sha256,
            "daily_basic_canonical_rows_sha256": daily_basic.canonical_rows_sha256,
            "daily_basic_filtered_codes_sha256": _canonical_sha256(
                list(basic_filtered)
            ),
            "daily_basic_normalization_receipt_sha256": (
                daily_basic.normalization_receipt_sha256
            ),
            "daily_basic_raw_codes_sha256": _canonical_sha256(
                list(basic_raw_codes)
            ),
            "daily_basic_raw_relative_path": daily_basic.raw_relative_path,
            "daily_basic_raw_row_count": len(basic_raw_codes),
            "daily_basic_raw_segment_counts": legacy._segment_counts(
                basic_raw_codes
            ),
            "daily_basic_raw_sha256": daily_basic.raw_sha256,
            "daily_basic_resolved_identities_sha256": _canonical_sha256(
                list(basic_identities)
            ),
            "daily_basic_source_normalization_rows_sha256": (
                daily_basic.source_normalization_rows_sha256
            ),
            "daily_basic_transition_excluded_codes_sha256": _canonical_sha256(
                list(basic_excluded)
            ),
            "daily_basic_transition_excluded_row_count": len(basic_excluded),
            "daily_basic_transition_overlap_comparison_fields": list(
                legacy._OVERLAP_FIELDS
            ),
            "daily_basic_transition_overlap_pair_count": len(overlap_proofs),
            "daily_basic_transition_overlap_root_sha256": overlap_root,
            "extra_daily_basic_code_count": 0,
            "missing_authoritative_daily_code_count": 0,
            "source_ts_code_exact_set_verified_after_transition_filter": True,
            "target_scope_codes_sha256": _canonical_sha256(target_codes),
            "target_scope_resolved_identities_sha256": _canonical_sha256(
                target_identities
            ),
            "target_scope_row_count": len(target_pairs),
            "trade_date": session,
            "transition_filtered_row_count": len(basic_filtered),
            "transition_resolved_identity_exact_set_verified": True,
        }
        per_date.append(stats)
        daily_identity_rows.append(
            {
                "filtered_codes_sha256": stats[
                    "authoritative_daily_filtered_codes_sha256"
                ],
                "generation_id": daily_partition.generation_id,
                "raw_codes_sha256": stats[
                    "authoritative_daily_raw_codes_sha256"
                ],
                "trade_date": session,
            }
        )
        normalized_authority_rows.append(
            {
                "canonical_rows_sha256": daily_basic.canonical_rows_sha256,
                "collection_set_sha256": daily_basic.collection_set_sha256,
                "raw_sha256": daily_basic.raw_sha256,
                "source_normalization_rows_sha256": (
                    daily_basic.source_normalization_rows_sha256
                ),
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
                "resolved_identities_sha256": stats[
                    "target_scope_resolved_identities_sha256"
                ],
                "row_count": len(target_pairs),
                "trade_date": session,
                "ts_codes_sha256": stats["target_scope_codes_sha256"],
            }
        )

    boundary_proofs = legacy._validate_transition_boundaries(
        trade_dates=sessions,
        filtered_codes_by_date=filtered_codes_by_date,
        transitions_by_code=transitions_by_code,
    )
    boundary_root = _canonical_sha256(boundary_proofs)
    daily_descriptor, transition_descriptor = legacy._authority_descriptors(
        source,
        transition,
    )
    trade_dates_sha256 = _canonical_sha256(sessions)
    per_date_root = _canonical_sha256(per_date)
    normalized_root = _canonical_sha256(normalized_authority_rows)
    target_root = _canonical_sha256(target_identity_rows)
    overlap_root = _canonical_sha256(overlap_authority_rows)
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
        "authority_scope": _AUTHORITY_SCOPE,
        "authority_status": _AUTHORITY_STATUS,
        "embargo_consumed": False,
        "exact_set_verified": True,
        "factor_v3_development_materialization_input_eligible": True,
        "factor_v3_target_identity_root_sha256": target_root,
        "factor_v3_target_scope": {
            "excluded_segments": ["SSE_STAR", "BSE"],
            "included_segments": ["SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"],
            "policy_id": legacy.POLICY_ID,
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
        "trade_date_count": len(sessions),
        "trade_dates": sessions,
        "trade_dates_sha256": trade_dates_sha256,
        "transition_boundary_authority_root_sha256": boundary_root,
        "transition_boundary_count": len(boundary_proofs),
        "transition_overlap_authority_root_sha256": overlap_root,
        "transition_resolved_identity_exact_set_verified": True,
    }
    receipt = {
        **unsigned,
        "authority_root_sha256": _canonical_sha256(unsigned),
    }
    _validate_v2_receipt(receipt)
    if receipt["audited_daily_authority"]["artifact_root_sha256"] != source.artifact_root_sha256:
        raise ValueError("factor-v3 daily-basic source authority drifted")
    if source_identity.get("session_count") != 733:
        raise ValueError("factor-v3 daily-basic source identity rejected")
    return receipt


def _artifact_path(root: Path, *, kind: str, relative_path: str, digest: str) -> Path:
    expected = _sha256_text(digest, label=f"{kind} sha256")
    pattern = re.compile(
        rf"{re.escape(_KINDS[kind])}/sha256/([0-9a-f]{{2}})/([0-9a-f]{{64}})\.json"
    )
    match = pattern.fullmatch(relative_path) if type(relative_path) is str else None
    if match is None or match.group(1) != expected[:2] or match.group(2) != expected:
        raise ValueError(f"factor-v3 daily-basic 733 {kind} path rejected")
    return raw_authority._safe_existing_file(
        root / Path(*relative_path.split("/")),
        f"factor-v3 daily-basic 733 {kind}",
    )


def _write_artifact(root: Path, *, kind: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    raw = _canonical_bytes(dict(payload))
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"factor-v3 daily-basic 733 {kind} too large")
    digest = _sha256(raw)
    directory = raw_authority._content_addressed_directory(root, _KINDS[kind], digest)
    path = directory / f"{digest}.json"
    raw_authority._write_create_only(
        path,
        raw,
        label=f"factor-v3 daily-basic 733 {kind}",
        reuse_identical=False,
    )
    return f"{_KINDS[kind]}/sha256/{digest[:2]}/{digest}.json", digest


def _read_artifact(
    root: Path, *, kind: str, relative_path: str, digest: str
) -> dict[str, Any]:
    path = _artifact_path(
        root,
        kind=kind,
        relative_path=relative_path,
        digest=digest,
    )
    raw = raw_authority._read_safe_file(
        path,
        label=f"factor-v3 daily-basic 733 {kind}",
        max_bytes=_MAX_ARTIFACT_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), digest):
        raise ValueError(f"factor-v3 daily-basic 733 {kind} content address rejected")
    value = legacy._strict_json_loads(raw)
    if type(value) is not dict or not hmac.compare_digest(raw, _canonical_bytes(value)):
        raise ValueError(f"factor-v3 daily-basic 733 {kind} canonical form rejected")
    return value


def _source_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: kwargs[key]
        for key in (
            "feature_history_run_spec_path",
            "feature_history_run_root",
            "feature_history_frozen_source_attestation_path",
            "expected_feature_history_frozen_source_attestation_sha256",
            "feature_history_frozen_source_root",
            "expected_feature_history_frozen_source_commit",
            "audited_development_universe_sqlite_path",
            "expected_development_coverage_audit_sha256",
            "expected_development_artifact_root_sha256",
            "expected_development_temporal_contract_sha256",
            "expected_development_temporal_role",
            "expected_source_authority_root_sha256",
        )
    }


def publish_factor_v3_daily_basic_733_exact_set_coverage(
    *,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    feature_history_frozen_source_attestation_path: str | Path,
    expected_feature_history_frozen_source_attestation_sha256: str,
    feature_history_frozen_source_root: str | Path,
    expected_feature_history_frozen_source_commit: str,
    audited_development_universe_sqlite_path: str | Path,
    expected_development_coverage_audit_sha256: str,
    expected_development_artifact_root_sha256: str,
    expected_development_temporal_contract_sha256: str,
    expected_development_temporal_role: str,
    expected_source_authority_root_sha256: str,
    points_output_root: str | Path,
    collection_set_refs: Sequence[Mapping[str, Any]],
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    output_root: str | Path,
) -> dict[str, Any]:
    kwargs = locals()
    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "factor-v3 daily-basic 733 output root",
    )
    source, source_identity = _load_733_authority(**_source_kwargs(kwargs))
    transition = _load_transition_authority(
        security_code_transition_evidence_root=security_code_transition_evidence_root,
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
    )
    capability_sha256 = _sha256(str(uuid.uuid4()).encode())
    receipt = _derive(
        source=source,
        source_identity=source_identity,
        transition=transition,
        points_output_root=points_output_root,
        collection_set_refs=collection_set_refs,
        publication_capability_sha256=capability_sha256,
    )
    receipt_relative, receipt_sha = _write_artifact(
        root,
        kind="receipt",
        payload=receipt,
    )
    replay = _derive(
        source=source,
        source_identity=source_identity,
        transition=transition,
        points_output_root=points_output_root,
        collection_set_refs=collection_set_refs,
        publication_capability_sha256=receipt["publication_capability_sha256"],
    )
    if replay != receipt:
        raise ValueError("factor-v3 daily-basic 733 receipt replay drifted")
    refs = _validated_refs(
        collection_set_refs,
        [item.trade_date for item in source.partitions],
    )
    attestation = {
        "authority_root_sha256": receipt["authority_root_sha256"],
        "collection_set_refs_sha256": _canonical_sha256(list(refs)),
        "producer_binding": _producer_binding(),
        "receipt_sha256": receipt_sha,
        "schema": ATTESTATION_SCHEMA,
        "source_authority_root_sha256": source_identity["root_sha256"],
        "trade_date_count": 733,
        "trade_dates_sha256": receipt["trade_dates_sha256"],
        "verified": True,
    }
    attestation_relative, attestation_sha = _write_artifact(
        root,
        kind="attestation",
        payload=attestation,
    )
    publication_manifest = {
        "attestation_relative_path": attestation_relative,
        "attestation_sha256": attestation_sha,
        "producer_binding": _producer_binding(),
        "receipt_relative_path": receipt_relative,
        "receipt_sha256": receipt_sha,
        "schema": PUBLICATION_MANIFEST_SCHEMA,
        "source_authority_root_sha256": source_identity["root_sha256"],
    }
    publication_relative, publication_sha = _write_artifact(
        root,
        kind="publication",
        payload=publication_manifest,
    )
    publication = {
        "attestation_relative_path": attestation_relative,
        "attestation_sha256": attestation_sha,
        "publication_relative_path": publication_relative,
        "publication_sha256": publication_sha,
        "receipt_relative_path": receipt_relative,
        "receipt_sha256": receipt_sha,
        "schema": PUBLICATION_SCHEMA,
    }
    verify_factor_v3_daily_basic_733_exact_set_coverage(
        **_source_kwargs(kwargs),
        points_output_root=points_output_root,
        collection_set_refs=collection_set_refs,
        security_code_transition_evidence_root=security_code_transition_evidence_root,
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        output_root=output_root,
        publication=publication,
    )
    return publication


def verify_factor_v3_daily_basic_733_exact_set_coverage(
    *,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    feature_history_frozen_source_attestation_path: str | Path,
    expected_feature_history_frozen_source_attestation_sha256: str,
    feature_history_frozen_source_root: str | Path,
    expected_feature_history_frozen_source_commit: str,
    audited_development_universe_sqlite_path: str | Path,
    expected_development_coverage_audit_sha256: str,
    expected_development_artifact_root_sha256: str,
    expected_development_temporal_contract_sha256: str,
    expected_development_temporal_role: str,
    expected_source_authority_root_sha256: str,
    points_output_root: str | Path,
    collection_set_refs: Sequence[Mapping[str, Any]],
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    output_root: str | Path,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    kwargs = locals()
    if (
        type(publication) is not dict
        or set(publication) != _PUBLICATION_FIELDS
        or publication.get("schema") != PUBLICATION_SCHEMA
    ):
        raise ValueError("factor-v3 daily-basic 733 publication rejected")
    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "factor-v3 daily-basic 733 output root",
    )
    receipt = _read_artifact(
        root,
        kind="receipt",
        relative_path=publication["receipt_relative_path"],
        digest=publication["receipt_sha256"],
    )
    attestation = _read_artifact(
        root,
        kind="attestation",
        relative_path=publication["attestation_relative_path"],
        digest=publication["attestation_sha256"],
    )
    publication_manifest = _read_artifact(
        root,
        kind="publication",
        relative_path=publication["publication_relative_path"],
        digest=publication["publication_sha256"],
    )
    if publication_manifest != {
        "attestation_relative_path": publication["attestation_relative_path"],
        "attestation_sha256": publication["attestation_sha256"],
        "producer_binding": _producer_binding(),
        "receipt_relative_path": publication["receipt_relative_path"],
        "receipt_sha256": publication["receipt_sha256"],
        "schema": PUBLICATION_MANIFEST_SCHEMA,
        "source_authority_root_sha256": attestation.get(
            "source_authority_root_sha256"
        ),
    }:
        raise ValueError("factor-v3 daily-basic 733 publication manifest rejected")
    source, source_identity = _load_733_authority(**_source_kwargs(kwargs))
    transition = _load_transition_authority(
        security_code_transition_evidence_root=security_code_transition_evidence_root,
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
    )
    rebuilt = _derive(
        source=source,
        source_identity=source_identity,
        transition=transition,
        points_output_root=points_output_root,
        collection_set_refs=collection_set_refs,
        publication_capability_sha256=receipt.get("publication_capability_sha256"),
    )
    if rebuilt != receipt or _sha256(_canonical_bytes(receipt)) != publication["receipt_sha256"]:
        raise ValueError("factor-v3 daily-basic 733 receipt replay rejected")
    refs = _validated_refs(
        collection_set_refs,
        [item.trade_date for item in source.partitions],
    )
    expected_attestation = {
        "authority_root_sha256": receipt["authority_root_sha256"],
        "collection_set_refs_sha256": _canonical_sha256(list(refs)),
        "producer_binding": _producer_binding(),
        "receipt_sha256": publication["receipt_sha256"],
        "schema": ATTESTATION_SCHEMA,
        "source_authority_root_sha256": source_identity["root_sha256"],
        "trade_date_count": 733,
        "trade_dates_sha256": receipt["trade_dates_sha256"],
        "verified": True,
    }
    if attestation != expected_attestation:
        raise ValueError("factor-v3 daily-basic 733 postverification attestation rejected")
    return {
        "authority_root_sha256": receipt["authority_root_sha256"],
        "receipt_sha256": publication["receipt_sha256"],
        "trade_date_count": 733,
        "trade_dates": list(receipt["trade_dates"]),
        "verified": True,
    }
