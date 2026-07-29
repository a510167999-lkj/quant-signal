from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

import pytest

from app import jiaoch_daily_basic_exact_set_authority as authority
from app.jiaoch_points_response_normalization import NormalizedDailyBasicRow


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _transition(
    predecessor: str = "300114.SZ",
    successor: str = "302132.SZ",
    *,
    effective_date: str = "2025-02-17",
    security_id: str = "cn-a-share:300114.SZ",
) -> dict[str, Any]:
    return {
        "canonical_ts_code": predecessor,
        "effective_date": effective_date,
        "predecessor_ts_code": predecessor,
        "security_id": security_id,
        "successor_ts_code": successor,
        "transition_id": _sha(f"{predecessor}:{successor}:{effective_date}"),
    }


def _transition_authority() -> authority.TransitionAuthority:
    transition = _transition()
    return authority.TransitionAuthority(
        contract_sha256=_sha("transition-contract"),
        evidence_receipt_sha256=_sha("transition-evidence-receipt"),
        evidence_receipts_sha256=_sha("transition-evidence-rows"),
        transition_count=1,
        transitions_by_code={
            transition["predecessor_ts_code"]: transition,
            transition["successor_ts_code"]: transition,
        },
    )


def _daily_authority() -> authority.AuditedDailyAuthority:
    first_codes = (
        "300114.SZ",
        "430001.BJ",
        "600001.SH",
        "688001.SH",
    )
    second_codes = (
        "302132.SZ",
        "430001.BJ",
        "600001.SH",
        "688001.SH",
    )
    partitions = (
        authority.AuthoritativeDailyPartition(
            trade_date="2025-02-14",
            generation_id=_sha("generation-1"),
            generation_manifest_sha256=_sha("generation-manifest-1"),
            generation_lineage_sha256=_sha("generation-lineage-1"),
            vintage="2025-02-14T16:00:00+08:00",
            ts_codes=first_codes,
        ),
        authority.AuthoritativeDailyPartition(
            trade_date="2025-02-17",
            generation_id=_sha("generation-2"),
            generation_manifest_sha256=_sha("generation-manifest-2"),
            generation_lineage_sha256=_sha("generation-lineage-2"),
            vintage="2025-02-17T16:00:00+08:00",
            ts_codes=second_codes,
        ),
    )
    market_generation_refs = [
        {
            "generation_id": item.generation_id,
            "lineage_sha256": item.generation_lineage_sha256,
            "manifest_sha256": item.generation_manifest_sha256,
            "trade_date": item.trade_date,
            "vintage": item.vintage,
        }
        for item in partitions
    ]
    return authority.AuditedDailyAuthority(
        manifest_file_sha256=_sha("audited-manifest-file"),
        manifest_sha256=_sha("audited-manifest"),
        bundle_sha256=_sha("audited-bundle"),
        artifact_root_sha256=_sha("audited-artifact-root"),
        sqlite_sha256=_sha("audited-sqlite"),
        coverage_audit_sha256=_sha("coverage-audit"),
        temporal_contract_sha256=_sha("temporal-contract"),
        temporal_role="development",
        daily_table_rows=8,
        daily_table_sha256=_sha("daily-table"),
        market_generation_count=2,
        market_generation_root_sha256=_canonical_sha(market_generation_refs),
        partitions=partitions,
    )


def _replace_daily_partitions(
    daily: authority.AuditedDailyAuthority,
    partitions: tuple[authority.AuthoritativeDailyPartition, ...],
) -> authority.AuditedDailyAuthority:
    refs = [
        {
            "generation_id": item.generation_id,
            "lineage_sha256": item.generation_lineage_sha256,
            "manifest_sha256": item.generation_manifest_sha256,
            "trade_date": item.trade_date,
            "vintage": item.vintage,
        }
        for item in partitions
    ]
    return replace(
        daily,
        daily_table_rows=sum(len(item.ts_codes) for item in partitions),
        market_generation_count=len(partitions),
        market_generation_root_sha256=_canonical_sha(refs),
        partitions=partitions,
    )


def _segment(code: str) -> tuple[str, bool]:
    if code.endswith(".BJ"):
        return "BSE", False
    if code.startswith(("688", "689")):
        return "SSE_STAR", False
    if code.startswith(("600", "601", "603", "605")):
        return "SSE_MAIN", True
    if code.startswith(("000", "001", "002", "003")):
        return "SZSE_MAIN", True
    if code.startswith(("300", "301", "302")):
        return "SZSE_CHINEXT", True
    raise AssertionError(code)


def _row(code: str, trade_date: str) -> NormalizedDailyBasicRow:
    segment, in_scope = _segment(code)
    return NormalizedDailyBasicRow(
        ts_code=code,
        trade_date=trade_date,
        turnover_rate=1.0,
        turnover_rate_f=2.0,
        free_share=3.0,
        float_share=4.0,
        total_mv=5.0,
        circ_mv=6.0,
        market_segment=segment,
        research_scope_mainboard_chinext=in_scope,
    )


def _collection_ref(trade_date: str) -> dict[str, str]:
    digest = _sha(f"collection:{trade_date}")
    return {
        "trade_date": trade_date,
        "collection_set_relative_path": (
            f"points_collection_sets/sha256/{digest[:2]}/{digest}.json"
        ),
        "collection_set_sha256": digest,
    }


def _daily_basic_partition(
    daily_partition: authority.AuthoritativeDailyPartition,
) -> authority.DailyBasicPartition:
    trade_date = daily_partition.trade_date
    collection = _collection_ref(trade_date)
    attempt = _sha(f"attempt:{trade_date}")
    raw = _sha(f"raw:{trade_date}")
    rows = tuple(_row(code, trade_date) for code in daily_partition.ts_codes)
    canonical_rows_sha256 = hashlib.sha256(
        json.dumps(
            [asdict(row) for row in rows],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return authority.DailyBasicPartition(
        trade_date=trade_date,
        collection_set_relative_path=collection["collection_set_relative_path"],
        collection_set_sha256=collection["collection_set_sha256"],
        attempt_relative_path=f"attempts/sha256/{attempt[:2]}/{attempt}.json",
        attempt_sha256=attempt,
        raw_relative_path=f"raw/sha256/{raw[:2]}/{raw}.body",
        raw_sha256=raw,
        source_normalization_rows_sha256=_sha(f"source-normalization:{trade_date}"),
        canonical_rows_sha256=canonical_rows_sha256,
        normalization_receipt_sha256=_sha(f"normalization-receipt:{trade_date}"),
        rows=rows,
    )


def _replace_partition_rows(
    partition: authority.DailyBasicPartition,
    rows: tuple[NormalizedDailyBasicRow, ...],
) -> authority.DailyBasicPartition:
    ordered = tuple(sorted(rows, key=lambda row: (row.trade_date, row.ts_code)))
    return replace(
        partition,
        canonical_rows_sha256=_canonical_sha([asdict(row) for row in ordered]),
        rows=ordered,
    )


def _install_authorities(
    monkeypatch: pytest.MonkeyPatch,
    *,
    daily: authority.AuditedDailyAuthority | None = None,
    daily_basic_by_date: dict[str, authority.DailyBasicPartition] | None = None,
    transitions: authority.TransitionAuthority | None = None,
) -> tuple[
    authority.AuditedDailyAuthority,
    dict[str, authority.DailyBasicPartition],
    authority.TransitionAuthority,
]:
    daily = daily or _daily_authority()
    daily_basic_by_date = daily_basic_by_date or {
        item.trade_date: _daily_basic_partition(item) for item in daily.partitions
    }
    transitions = transitions or _transition_authority()
    monkeypatch.setattr(
        authority,
        "_load_audited_daily_authority",
        lambda **_kwargs: daily,
    )

    def load_partition(
        *,
        collection_ref: dict[str, str],
        **_kwargs: Any,
    ) -> authority.DailyBasicPartition:
        return daily_basic_by_date[collection_ref["trade_date"]]

    monkeypatch.setattr(authority, "_load_daily_basic_partition", load_partition)
    monkeypatch.setattr(
        authority,
        "_load_transition_authority",
        lambda **_kwargs: transitions,
    )
    return daily, daily_basic_by_date, transitions


def _kwargs(
    tmp_path: Path,
    daily: authority.AuditedDailyAuthority | None = None,
) -> dict[str, Any]:
    daily = daily or _daily_authority()
    points_root = tmp_path / "points"
    evidence_root = tmp_path / "transition"
    output_root = tmp_path / "receipts"
    points_root.mkdir(exist_ok=True)
    evidence_root.mkdir(exist_ok=True)
    output_root.mkdir(exist_ok=True)
    return {
        "audited_universe_sqlite_path": tmp_path / "audited" / "metadata.sqlite3",
        "expected_coverage_audit_sha256": daily.coverage_audit_sha256,
        "expected_artifact_root_sha256": daily.artifact_root_sha256,
        "expected_temporal_contract_sha256": daily.temporal_contract_sha256,
        "expected_temporal_role": daily.temporal_role,
        "points_output_root": points_root,
        "collection_set_refs": [_collection_ref(item.trade_date) for item in daily.partitions],
        "security_code_transition_evidence_root": evidence_root,
        "expected_security_code_transition_contract_sha256": (
            _transition_authority().contract_sha256
        ),
        "output_root": output_root,
    }


def _receipt_path(publication: dict[str, Any], output_root: Path) -> Path:
    return output_root / Path(*publication["receipt_relative_path"].split("/"))


def test_public_entrypoints_are_offline_and_do_not_accept_credentials() -> None:
    publish = inspect.signature(authority.publish_daily_basic_exact_set_coverage)
    verify = inspect.signature(authority.verify_daily_basic_exact_set_coverage)
    forbidden = {
        "api_key",
        "credential",
        "token",
        "transport",
        "clock",
        "generation_id",
        "embargo",
        "final_oos",
        "production",
    }
    assert forbidden.isdisjoint(publish.parameters)
    assert forbidden.isdisjoint(verify.parameters)
    assert set(publish.parameters) == {
        "audited_universe_sqlite_path",
        "expected_coverage_audit_sha256",
        "expected_artifact_root_sha256",
        "expected_temporal_contract_sha256",
        "expected_temporal_role",
        "points_output_root",
        "collection_set_refs",
        "security_code_transition_evidence_root",
        "expected_security_code_transition_contract_sha256",
        "output_root",
    }
    assert set(verify.parameters) == {
        "audited_universe_sqlite_path",
        "expected_coverage_audit_sha256",
        "expected_artifact_root_sha256",
        "expected_temporal_contract_sha256",
        "expected_temporal_role",
        "points_output_root",
        "security_code_transition_evidence_root",
        "expected_security_code_transition_contract_sha256",
        "output_root",
        "receipt_relative_path",
        "expected_receipt_sha256",
        "publication_capability",
    }


def test_daily_basic_loader_converts_source_date_and_binds_both_normalized_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points_root = tmp_path / "points"
    points_root.mkdir()
    fields = authority.raw_authority._FIELDS_BY_API["daily_basic"].split(",")
    body = json.dumps(
        {
            "code": 0,
            "data": {
                "fields": fields,
                "items": [
                    ["600001.SH", "20250214", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    ["688001.SH", "20250214", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    ["430001.BJ", "20250214", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                ],
            },
            "msg": "success",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    raw_sha256 = hashlib.sha256(body).hexdigest()
    raw_relative_path = f"raw/sha256/{raw_sha256[:2]}/{raw_sha256}.body"
    raw_path = points_root / Path(*raw_relative_path.split("/"))
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(body)
    collection_ref = _collection_ref("2025-02-14")
    attempt_sha256 = _sha("real-normalization-attempt")
    monkeypatch.setattr(
        authority.points_collection,
        "_verify_and_load_collection_set",
        lambda **_kwargs: {
            "attempts": [
                {
                    "api_name": "daily_basic",
                    "attempt_relative_path": (
                        f"attempts/sha256/{attempt_sha256[:2]}/{attempt_sha256}.json"
                    ),
                    "attempt_sha256": attempt_sha256,
                    "raw_relative_path": raw_relative_path,
                    "raw_sha256": raw_sha256,
                }
            ],
            "trade_date": "2025-02-14",
        },
    )

    loaded = authority._load_daily_basic_partition(
        points_output_root=points_root,
        collection_ref=collection_ref,
    )

    assert loaded.trade_date == "2025-02-14"
    assert {row.trade_date for row in loaded.rows} == {"2025-02-14"}
    assert loaded.source_normalization_rows_sha256 != loaded.canonical_rows_sha256
    assert loaded.canonical_rows_sha256 == _canonical_sha([asdict(row) for row in loaded.rows])
    assert [row.market_segment for row in loaded.rows] == [
        "BSE",
        "SSE_MAIN",
        "SSE_STAR",
    ]


def test_audited_daily_loader_binds_every_manifest_generation_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "audited-bundle"
    bundle.mkdir()
    database_path = bundle / "metadata.sqlite3"
    database_path.write_bytes(b"test-placeholder")
    manifest_raw = b'{"fixture":"audited-manifest"}'
    (bundle / "manifest.json").write_bytes(manifest_raw)
    refs = [
        {
            "generation_id": _sha("loader-generation-1"),
            "lineage_sha256": _sha("loader-lineage-1"),
            "manifest_sha256": _sha("loader-manifest-1"),
            "trade_date": "2025-02-14",
            "vintage": "2025-02-14T16:00:00+08:00",
        },
        {
            "generation_id": _sha("loader-generation-2"),
            "lineage_sha256": _sha("loader-lineage-2"),
            "manifest_sha256": _sha("loader-manifest-2"),
            "trade_date": "2025-02-17",
            "vintage": "2025-02-17T16:00:00+08:00",
        },
    ]
    rows = [
        ("2025-02-14", refs[0]["generation_id"], "300114.SZ"),
        ("2025-02-14", refs[0]["generation_id"], "302132.SZ"),
        ("2025-02-14", refs[0]["generation_id"], "430001.BJ"),
        ("2025-02-17", refs[1]["generation_id"], "302132.SZ"),
        ("2025-02-17", refs[1]["generation_id"], "688001.SH"),
    ]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE market_session_generation_rows_daily (
            trade_date TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            ts_code TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO market_session_generation_rows_daily VALUES (?, ?, ?)",
        rows,
    )
    manifest = {
        "artifact_root_sha256": _sha("loader-artifact"),
        "bundle_sha256": _sha("loader-bundle"),
        "coverage_audit_sha256": _sha("loader-coverage"),
        "manifest_sha256": _sha("loader-manifest"),
        "market_generations": {
            "count": len(refs),
            "refs": refs,
            "root_sha256": _canonical_sha(refs),
        },
        "sqlite": {"sha256": _sha("loader-sqlite")},
        "tables": {
            "market_session_generation_rows_daily": {
                "rows": len(rows),
                "sha256": _sha("loader-daily-table"),
            }
        },
        "temporal_binding": {
            "contract_sha256": _sha("loader-temporal"),
            "role": "development",
        },
    }

    class FakeUniverse:
        def __init__(self) -> None:
            self.database_path = database_path
            self.manifest = manifest

        def __enter__(self) -> "FakeUniverse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def _require_open(self) -> sqlite3.Connection:
            return connection

    captured: dict[str, Any] = {}

    def from_file(
        _cls: type,
        path: str,
        **kwargs: Any,
    ) -> FakeUniverse:
        captured.update({"path": path, **kwargs})
        return FakeUniverse()

    monkeypatch.setattr(
        authority.AuditedPointInTimeUniverse,
        "from_file",
        classmethod(from_file),
    )

    loaded = authority._load_audited_daily_authority(
        audited_universe_sqlite_path=database_path,
        expected_coverage_audit_sha256=manifest["coverage_audit_sha256"],
        expected_artifact_root_sha256=manifest["artifact_root_sha256"],
        expected_temporal_contract_sha256=manifest["temporal_binding"]["contract_sha256"],
        expected_temporal_role="development",
    )

    assert captured["path"] == str(database_path)
    assert loaded.manifest_file_sha256 == hashlib.sha256(manifest_raw).hexdigest()
    assert loaded.market_generation_root_sha256 == _canonical_sha(refs)
    assert [partition.trade_date for partition in loaded.partitions] == [
        "2025-02-14",
        "2025-02-17",
    ]
    assert loaded.partitions[0].ts_codes == (
        "300114.SZ",
        "302132.SZ",
        "430001.BJ",
    )
    assert loaded.partitions[1].ts_codes == ("302132.SZ", "688001.SH")


def test_transition_loader_binds_frozen_contract_and_evidence_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = authority.code_transition.SECURITY_CODE_TRANSITION_CONTRACT
    contract_sha256 = authority.code_transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    evidence_receipts = [{"raw_sha256": _sha("transition-pdf")}]
    receipt_identity = {
        "schema_version": "security-code-transition-evidence-load/v1",
        "contract_sha256": contract_sha256,
        "evidence_receipts": evidence_receipts,
    }
    monkeypatch.setattr(
        authority.code_transition,
        "load_security_code_transition_evidence",
        lambda *_args, **_kwargs: {
            **receipt_identity,
            "contract": contract,
            "receipt_sha256": authority.code_transition.canonical_sha256(receipt_identity),
        },
    )

    loaded = authority._load_transition_authority(
        security_code_transition_evidence_root=tmp_path,
        expected_security_code_transition_contract_sha256=contract_sha256,
    )

    assert loaded.contract_sha256 == contract_sha256
    assert loaded.transition_count == 1
    assert set(loaded.transitions_by_code) == {"300114.SZ", "302132.SZ"}
    assert loaded.transitions_by_code["302132.SZ"]["effective_date"] == "2025-02-17"


def test_publishes_full_market_exact_set_then_derives_target_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)

    publication = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    verified = authority.verify_daily_basic_exact_set_coverage(
        **{key: value for key, value in kwargs.items() if key != "collection_set_refs"},
        receipt_relative_path=publication["receipt_relative_path"],
        expected_receipt_sha256=publication["receipt_sha256"],
        publication_capability=publication["publication_capability"],
    )

    assert verified["verified"] is True
    assert verified["authority_status"] == "VERIFIED_DEVELOPMENT_DAILY_BASIC_EXACT_SET"
    assert verified["trade_dates"] == ["2025-02-14", "2025-02-17"]
    assert verified["trade_date_count"] == 2
    assert verified["exact_set_verified"] is True
    assert verified["raw_source_rows_bound"] is True
    assert verified["source_ts_code_exact_set_verified_after_transition_filter"] is True
    assert verified["transition_resolved_identity_exact_set_verified"] is True
    assert verified["all_supported_segments_compared_before_scope_filter"] is True
    assert verified["factor_v3_target_scope"] == {
        "excluded_segments": ["SSE_STAR", "BSE"],
        "included_segments": ["SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"],
        "policy_id": "research-mainboard-chinext/v1",
        "target_identity_row_count": 4,
    }
    assert len(verified["per_date_statistics"]) == 2
    for item in verified["per_date_statistics"]:
        assert item["authoritative_daily_raw_row_count"] == 4
        assert item["daily_basic_raw_row_count"] == 4
        assert item["transition_filtered_row_count"] == 4
        assert item["target_scope_row_count"] == 2
        assert item["authoritative_daily_raw_segment_counts"] == {
            "BSE": 1,
            "SSE_MAIN": 1,
            "SSE_STAR": 1,
            "SZSE_CHINEXT": 1,
            "SZSE_MAIN": 0,
        }
        assert (
            item["daily_basic_raw_segment_counts"] == item["authoritative_daily_raw_segment_counts"]
        )
        assert item["missing_authoritative_daily_code_count"] == 0
        assert item["extra_daily_basic_code_count"] == 0
    assert (
        verified["security_code_transition_authority"]["contract_sha256"]
        == transitions.contract_sha256
    )
    assert verified["audited_daily_authority"]["daily_table_rows"] == 8
    assert verified["audited_daily_authority"]["market_generation_count"] == len(daily.partitions)
    assert verified["row_authority_status"] == ("GRANTED_FOR_BOUND_DEVELOPMENT_COVERAGE_ONLY")
    assert verified["factor_v3_development_materialization_input_eligible"] is True
    assert verified["formal_factor_v3_materialization_performed"] is False
    assert verified["arbitrary_row_drops_permitted"] is False
    assert verified["silent_row_drops_permitted"] is False
    assert verified["rows_published"] is False
    assert verified["embargo_consumed"] is False
    assert verified["final_oos_consumed"] is False
    assert verified["production_profile_registered"] is False
    assert verified["production_recommendation_eligible"] is False
    assert {item["path"] for item in verified["producer_binding"]["entries"]} == {
        "app/jiaoch_daily_basic_exact_set_authority.py",
        "app/jiaoch_points_collection_set.py",
        "app/jiaoch_points_raw_authority.py",
        "app/jiaoch_points_response_normalization.py",
        "app/research_pit_store.py",
        "app/research_scope.py",
        "app/research_security_code_transition.py",
    }


def test_authoritative_transition_backfill_is_excluded_before_exact_set_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    first = daily.partitions[0]
    first_with_backfill = replace(
        first,
        ts_codes=tuple(sorted((*first.ts_codes, "302132.SZ"))),
    )
    daily = replace(
        daily,
        daily_table_rows=9,
        partitions=(first_with_backfill, daily.partitions[1]),
    )
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    publication = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    verified = authority.verify_daily_basic_exact_set_coverage(
        **{key: value for key, value in kwargs.items() if key != "collection_set_refs"},
        receipt_relative_path=publication["receipt_relative_path"],
        expected_receipt_sha256=publication["receipt_sha256"],
        publication_capability=publication["publication_capability"],
    )

    first_stats = verified["per_date_statistics"][0]
    assert first_stats["authoritative_daily_raw_row_count"] == 5
    assert first_stats["daily_basic_raw_row_count"] == 5
    assert first_stats["authoritative_daily_transition_excluded_row_count"] == 1
    assert first_stats["daily_basic_transition_excluded_row_count"] == 1
    assert first_stats["transition_filtered_row_count"] == 4
    assert first_stats["daily_basic_transition_overlap_pair_count"] == 1
    assert first_stats["daily_basic_transition_overlap_comparison_fields"] == [
        "turnover_rate",
        "turnover_rate_f",
        "free_share",
        "float_share",
        "total_mv",
        "circ_mv",
    ]
    assert verified["source_missingness"] == {
        "extra_daily_basic_code_count": 0,
        "missing_authoritative_daily_code_count": 0,
        "status": "NONE_AFTER_AUTHORIZED_TRANSITION_FILTER",
        "unproven_source_missingness_count": 0,
    }


@pytest.mark.parametrize("field", ["turnover_rate_f", "free_share"])
def test_transition_overlap_factor_conflict_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    daily = _daily_authority()
    first = daily.partitions[0]
    first_with_backfill = replace(
        first,
        ts_codes=tuple(sorted((*first.ts_codes, "302132.SZ"))),
    )
    daily = replace(
        daily,
        daily_table_rows=9,
        partitions=(first_with_backfill, daily.partitions[1]),
    )
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first_basic = partitions[first.trade_date]
    successor = next(row for row in first_basic.rows if row.ts_code == "302132.SZ")
    conflicting = replace(
        successor,
        **{field: getattr(successor, field) + 0.25},
    )
    partitions[first.trade_date] = _replace_partition_rows(
        first_basic,
        tuple(conflicting if row.ts_code == successor.ts_code else row for row in first_basic.rows),
    )
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match=field):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_transition_overlap_large_value_near_conflict_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    first = daily.partitions[0]
    first_with_backfill = replace(
        first,
        ts_codes=tuple(sorted((*first.ts_codes, "302132.SZ"))),
    )
    daily = replace(
        daily,
        daily_table_rows=9,
        partitions=(first_with_backfill, daily.partitions[1]),
    )
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first_basic = partitions[first.trade_date]
    rows = tuple(
        replace(row, total_mv=1_000_000_000_000.0)
        if row.ts_code == "300114.SZ"
        else replace(row, total_mv=1_000_000_000_000.5)
        if row.ts_code == "302132.SZ"
        else row
        for row in first_basic.rows
    )
    partitions[first.trade_date] = _replace_partition_rows(first_basic, rows)
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="total_mv"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_transition_boundary_session_cannot_be_missing_from_full_date_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    second = replace(
        daily.partitions[1],
        trade_date="2025-02-18",
        vintage="2025-02-18T16:00:00+08:00",
    )
    daily = _replace_daily_partitions(
        daily,
        (daily.partitions[0], second),
    )
    _install_authorities(monkeypatch, daily=daily)
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="transition boundary session"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


@pytest.mark.parametrize(
    ("partition_index", "missing_role"),
    [(0, "predecessor"), (1, "successor")],
)
def test_transition_boundary_requires_both_authoritative_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partition_index: int,
    missing_role: str,
) -> None:
    daily = _daily_authority()
    partitions = list(daily.partitions)
    target = partitions[partition_index]
    transition_code = "300114.SZ" if partition_index == 0 else "302132.SZ"
    partitions[partition_index] = replace(
        target,
        ts_codes=tuple(
            sorted("300001.SZ" if code == transition_code else code for code in target.ts_codes)
        ),
    )
    daily = _replace_daily_partitions(daily, tuple(partitions))
    _install_authorities(monkeypatch, daily=daily)
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(
        ValueError,
        match=f"transition boundary {missing_role}",
    ):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_publication_returns_unique_postverified_candidate_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)

    first = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    second = authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert first["receipt_created"] is True
    assert second["receipt_created"] is True
    assert first["receipt_sha256"] != second["receipt_sha256"]
    assert first["publication_capability"] != second["publication_capability"]
    assert first["publication_status"] == "DURABLE_POSTVERIFIED_AND_RETURNED"
    assert first["schema"] == "daily-basic-exact-set-publication/v1"
    path = _receipt_path(first, kwargs["output_root"])
    assert path.read_bytes() == authority._canonical_json(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first["receipt_sha256"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda refs: refs[1:],
        lambda refs: [*refs, _collection_ref("2025-02-18")],
        lambda refs: [refs[0], refs[0]],
        lambda refs: [
            {**refs[0], "trade_date": "2025-02-15"},
            refs[1],
        ],
    ],
    ids=["missing-date", "extra-date", "duplicate-date", "date-drift"],
)
def test_rejects_any_collection_ref_set_drift_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    kwargs["collection_set_refs"] = mutate(kwargs["collection_set_refs"])

    with pytest.raises(ValueError, match="collection.*date|trade date"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


@pytest.mark.parametrize("difference", ["missing", "extra"])
def test_any_raw_ts_code_difference_blocks_the_whole_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    difference: str,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    rows = list(first.rows)
    if difference == "missing":
        rows.pop()
    else:
        rows.append(_row("600002.SH", first.trade_date))
    partitions[first.trade_date] = _replace_partition_rows(first, tuple(rows))
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="exact set"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_duplicate_daily_basic_identity_blocks_the_whole_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    partitions[first.trade_date] = _replace_partition_rows(
        first,
        (*first.rows, first.rows[0]),
    )
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="duplicate"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_daily_basic_row_date_drift_blocks_the_whole_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    partitions[first.trade_date] = _replace_partition_rows(
        first,
        (replace(first.rows[0], trade_date="2025-02-13"), *first.rows[1:]),
    )
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="trade_date"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("market_segment", "SSE_STAR"),
        ("research_scope_mainboard_chinext", False),
    ],
)
def test_normalized_segment_or_scope_drift_is_not_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    target = next(row for row in first.rows if row.ts_code == "600001.SH")
    replacement = replace(target, **{field: value})
    partitions[first.trade_date] = _replace_partition_rows(
        first,
        tuple(replacement if row is target else row for row in first.rows),
    )
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )

    with pytest.raises(ValueError, match="segment|scope"):
        authority.publish_daily_basic_exact_set_coverage(**_kwargs(tmp_path, daily))


def test_transition_provider_backfill_is_rejected_even_if_stable_identity_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    rows = tuple(
        _row("302132.SZ", first.trade_date) if row.ts_code == "300114.SZ" else row
        for row in first.rows
    )
    partitions[first.trade_date] = _replace_partition_rows(first, rows)
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )

    with pytest.raises(ValueError, match="transition|exact set"):
        authority.publish_daily_basic_exact_set_coverage(**_kwargs(tmp_path, daily))


@pytest.mark.parametrize(
    ("partition_index", "correct_code", "wrong_code"),
    [
        (0, "300114.SZ", "302132.SZ"),
        (1, "302132.SZ", "300114.SZ"),
    ],
)
def test_wrong_period_alias_cannot_disappear_from_both_sources_without_counterpart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partition_index: int,
    correct_code: str,
    wrong_code: str,
) -> None:
    daily = _daily_authority()
    partitions = list(daily.partitions)
    target = partitions[partition_index]
    partitions[partition_index] = replace(
        target,
        ts_codes=tuple(
            sorted(wrong_code if code == correct_code else code for code in target.ts_codes)
        ),
    )
    daily = _replace_daily_partitions(daily, tuple(partitions))
    _install_authorities(monkeypatch, daily=daily)
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="correct counterpart"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_transition_resolution_collision_blocks_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    collision = _transition(
        predecessor="600001.SH",
        successor="600002.SH",
        effective_date="2025-02-17",
        security_id="cn-a-share:300114.SZ",
    )
    original = _transition()
    transitions = _transition_authority()
    transitions = replace(
        transitions,
        transition_count=2,
        transitions_by_code={
            original["predecessor_ts_code"]: original,
            original["successor_ts_code"]: original,
            collision["predecessor_ts_code"]: collision,
            collision["successor_ts_code"]: collision,
        },
    )
    _install_authorities(monkeypatch, daily=daily, transitions=transitions)

    with pytest.raises(ValueError, match="resolved.*duplicate|identity"):
        authority.publish_daily_basic_exact_set_coverage(**_kwargs(tmp_path, daily))


def test_tampered_receipt_or_source_binding_cannot_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    publication = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    path = _receipt_path(publication, kwargs["output_root"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trade_date_count"] = 1
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="content address|sha256"):
        authority.verify_daily_basic_exact_set_coverage(
            **{key: value for key, value in kwargs.items() if key != "collection_set_refs"},
            receipt_relative_path=publication["receipt_relative_path"],
            expected_receipt_sha256=publication["receipt_sha256"],
            publication_capability=publication["publication_capability"],
        )


def test_verified_receipt_fails_when_any_bound_input_drifts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, daily_basic, transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    publication = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    monkeypatch.setattr(
        authority,
        "_load_audited_daily_authority",
        lambda **_kwargs: replace(
            daily,
            daily_table_sha256=_sha("drifted-daily-table"),
        ),
    )
    monkeypatch.setattr(
        authority,
        "_load_transition_authority",
        lambda **_kwargs: transitions,
    )
    monkeypatch.setattr(
        authority,
        "_load_daily_basic_partition",
        lambda *, collection_ref, **_kwargs: daily_basic[collection_ref["trade_date"]],
    )

    with pytest.raises(ValueError, match="replay|binding|descriptor"):
        authority.verify_daily_basic_exact_set_coverage(
            **{key: value for key, value in kwargs.items() if key != "collection_set_refs"},
            receipt_relative_path=publication["receipt_relative_path"],
            expected_receipt_sha256=publication["receipt_sha256"],
            publication_capability=publication["publication_capability"],
        )


def test_failed_postpublication_verification_leaves_only_unreferenced_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    monkeypatch.setattr(
        authority,
        "verify_daily_basic_exact_set_coverage",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("forced self verify failure")),
    )

    with pytest.raises(ValueError, match="forced self verify failure"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    candidates = list(kwargs["output_root"].rglob("*.json"))
    assert len(candidates) == 1
    assert b"publication_capability_sha256" in candidates[0].read_bytes()
    assert b'"publication_capability":' not in candidates[0].read_bytes()


def test_foreign_replacement_after_candidate_write_is_never_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    original = authority._write_receipt_candidate_create_only
    captured: dict[str, Path] = {}

    def replace_after_write(path: Path, raw: bytes) -> bool:
        created = original(path, raw)
        captured["path"] = path
        path.write_bytes(b"foreign replacement")
        return created

    monkeypatch.setattr(
        authority,
        "_write_receipt_candidate_create_only",
        replace_after_write,
    )

    with pytest.raises(ValueError, match="content address"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert captured["path"].read_bytes() == b"foreign replacement"


def test_verifier_requires_the_explicit_returned_publication_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)
    publication = authority.publish_daily_basic_exact_set_coverage(**kwargs)

    with pytest.raises(ValueError, match="publication capability"):
        authority.verify_daily_basic_exact_set_coverage(
            **{key: value for key, value in kwargs.items() if key != "collection_set_refs"},
            receipt_relative_path=publication["receipt_relative_path"],
            expected_receipt_sha256=publication["receipt_sha256"],
            publication_capability=str(uuid.uuid4()),
        )


def test_source_missingness_cannot_be_converted_to_candidate_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    partitions[first.trade_date] = _replace_partition_rows(
        first,
        tuple(row for row in first.rows if row.ts_code != "600001.SH"),
    )
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )
    kwargs = _kwargs(tmp_path, daily)

    with pytest.raises(ValueError, match="exact set"):
        authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert not list(kwargs["output_root"].rglob("*"))
