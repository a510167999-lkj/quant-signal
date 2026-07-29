from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app import jiaoch_daily_basic_exact_set_authority as authority
from app.jiaoch_points_response_normalization import NormalizedDailyBasicRow


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


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
        market_generation_root_sha256=_sha("market-generations"),
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
        canonical_rows_sha256=canonical_rows_sha256,
        normalization_receipt_sha256=_sha(f"normalization-receipt:{trade_date}"),
        rows=rows,
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
    }


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
    partitions[first.trade_date] = replace(
        first_basic,
        rows=tuple(
            conflicting if row.ts_code == successor.ts_code else row for row in first_basic.rows
        ),
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


def test_publication_is_content_addressed_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, _daily_basic, _transitions = _install_authorities(monkeypatch)
    kwargs = _kwargs(tmp_path, daily)

    first = authority.publish_daily_basic_exact_set_coverage(**kwargs)
    second = authority.publish_daily_basic_exact_set_coverage(**kwargs)

    assert first["receipt_created"] is True
    assert second == {**first, "receipt_created": False}
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
    partitions[first.trade_date] = replace(first, rows=tuple(rows))
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
    partitions[first.trade_date] = replace(
        first,
        rows=(*first.rows, first.rows[0]),
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
    partitions[first.trade_date] = replace(
        first,
        rows=(replace(first.rows[0], trade_date="2025-02-13"), *first.rows[1:]),
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
    partitions[first.trade_date] = replace(
        first,
        rows=tuple(replacement if row is target else row for row in first.rows),
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
    partitions[first.trade_date] = replace(first, rows=rows)
    _install_authorities(
        monkeypatch,
        daily=daily,
        daily_basic_by_date=partitions,
    )

    with pytest.raises(ValueError, match="transition|exact set"):
        authority.publish_daily_basic_exact_set_coverage(**_kwargs(tmp_path, daily))


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
        )


def test_failed_post_publication_self_verification_rolls_back_only_created_receipt(
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

    assert not list(kwargs["output_root"].rglob("*.json"))


def test_source_missingness_cannot_be_converted_to_candidate_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_authority()
    partitions = {item.trade_date: _daily_basic_partition(item) for item in daily.partitions}
    first = partitions["2025-02-14"]
    partitions[first.trade_date] = replace(
        first,
        rows=tuple(row for row in first.rows if row.ts_code != "600001.SH"),
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
