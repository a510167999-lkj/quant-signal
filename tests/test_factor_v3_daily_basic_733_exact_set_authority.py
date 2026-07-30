from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app import factor_v3_daily_basic_733_exact_set_authority as authority
from app import jiaoch_daily_basic_exact_set_authority as legacy
from app.jiaoch_points_response_normalization import NormalizedDailyBasicRow


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _sessions() -> tuple[list[str], list[str]]:
    development_start = date(2024, 7, 5)
    holidays = frozenset(
        """
        2024-09-16 2024-09-17
        2024-10-01 2024-10-02 2024-10-03 2024-10-04 2024-10-07
        2025-01-01
        2025-01-28 2025-01-29 2025-01-30 2025-01-31 2025-02-03 2025-02-04
        2025-04-04
        2025-05-01 2025-05-02 2025-05-05
        2025-06-02
        2025-10-01 2025-10-02 2025-10-03 2025-10-06 2025-10-07 2025-10-08
        2026-01-01 2026-01-02
        2026-02-16 2026-02-17 2026-02-18 2026-02-19 2026-02-20 2026-02-23
        2026-04-06
        2026-05-01 2026-05-04 2026-05-05
        2026-06-19
        """.split()
    )
    development: list[str] = []
    cursor = development_start
    end = date(2026, 7, 3)
    while cursor <= end:
        if cursor.weekday() < 5 and cursor.isoformat() not in holidays:
            development.append(cursor.isoformat())
        cursor += timedelta(days=1)
    prewindow: list[str] = []
    cursor = development_start - timedelta(days=1)
    while len(prewindow) < 250:
        if cursor.weekday() < 5:
            prewindow.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    prewindow.reverse()
    assert len(prewindow) == 250
    assert len(development) == 483
    return prewindow, development


def _daily_authority(sessions: list[str], label: str) -> legacy.AuditedDailyAuthority:
    partitions = tuple(
        legacy.AuthoritativeDailyPartition(
            trade_date=session,
            generation_id=f"{label}-{index}",
            generation_manifest_sha256=_sha(f"{label}:manifest:{session}"),
            generation_lineage_sha256=_sha(f"{label}:lineage:{session}"),
            vintage=f"{session}T16:00:00+08:00",
            ts_codes=("600001.SH",),
        )
        for index, session in enumerate(sessions)
    )
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
    return legacy.AuditedDailyAuthority(
        manifest_file_sha256=_sha(f"{label}:manifest-file"),
        manifest_sha256=_sha(f"{label}:manifest"),
        bundle_sha256=_sha(f"{label}:bundle"),
        artifact_root_sha256=_sha(f"{label}:artifact"),
        sqlite_sha256=_sha(f"{label}:sqlite"),
        coverage_audit_sha256=_sha(f"{label}:coverage"),
        temporal_contract_sha256=_sha(f"{label}:temporal"),
        temporal_role="development",
        daily_table_rows=len(partitions),
        daily_table_sha256=_canonical_sha(
            [{"trade_date": item.trade_date, "ts_codes": list(item.ts_codes)} for item in partitions]
        ),
        market_generation_count=len(partitions),
        market_generation_root_sha256=_canonical_sha(refs),
        partitions=partitions,
    )


def _ref(session: str, *, legacy_path: bool = False) -> dict[str, str]:
    digest = _sha(f"collection:{session}")
    kind = "points_collection_sets" if legacy_path else "daily_basic_collection_sets"
    return {
        "trade_date": session,
        "collection_set_relative_path": f"{kind}/sha256/{digest[:2]}/{digest}.json",
        "collection_set_sha256": digest,
    }


def _daily_basic_partition(session: str) -> legacy.DailyBasicPartition:
    ref = _ref(session)
    row = NormalizedDailyBasicRow(
        ts_code="600001.SH",
        trade_date=session,
        turnover_rate=1.0,
        turnover_rate_f=2.0,
        free_share=3.0,
        float_share=4.0,
        total_mv=5.0,
        circ_mv=6.0,
        market_segment="SSE_MAIN",
        research_scope_mainboard_chinext=True,
    )
    attempt = _sha(f"attempt:{session}")
    raw = _sha(f"raw:{session}")
    return legacy.DailyBasicPartition(
        trade_date=session,
        collection_set_relative_path=ref["collection_set_relative_path"],
        collection_set_sha256=ref["collection_set_sha256"],
        attempt_relative_path=f"attempts/sha256/{attempt[:2]}/{attempt}.json",
        attempt_sha256=attempt,
        raw_relative_path=f"raw/sha256/{raw[:2]}/{raw}.body",
        raw_sha256=raw,
        source_normalization_rows_sha256=_sha(f"source:{session}"),
        canonical_rows_sha256=_canonical_sha([asdict(row)]),
        normalization_receipt_sha256=_sha(f"normalization:{session}"),
        rows=(row,),
    )


def _transition_authority() -> legacy.TransitionAuthority:
    descriptor = {
        "canonical_ts_code": "300114.SZ",
        "effective_date": "2099-01-01",
        "predecessor_ts_code": "300114.SZ",
        "security_id": "cn-a-share:300114.SZ",
        "successor_ts_code": "302132.SZ",
        "transition_id": _sha("unrelated-transition"),
    }
    return legacy.TransitionAuthority(
        contract_sha256=_sha("transition-contract"),
        evidence_receipt_sha256=_sha("transition-receipt"),
        evidence_receipts_sha256=_sha("transition-rows"),
        transition_count=1,
        transitions_by_code={
            descriptor["predecessor_ts_code"]: descriptor,
            descriptor["successor_ts_code"]: descriptor,
        },
    )


def _install_sources(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    prewindow, development = _sessions()
    monkeypatch.setattr(
        authority,
        "_load_feature_history_prewindow_authority",
        lambda **_kwargs: _daily_authority(prewindow, "prewindow"),
    )
    monkeypatch.setattr(
        authority,
        "_load_development_authority",
        lambda **_kwargs: _daily_authority(development, "development"),
    )
    monkeypatch.setattr(
        authority,
        "_load_transition_authority",
        lambda **_kwargs: _transition_authority(),
    )
    monkeypatch.setattr(
        legacy,
        "_load_daily_basic_partition",
        lambda *, collection_ref, **_kwargs: _daily_basic_partition(collection_ref["trade_date"]),
    )
    return prewindow, development


def _kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], list[str], list[str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    prewindow, development = _install_sources(monkeypatch)
    points_root = tmp_path / "points"
    output_root = tmp_path / "authority"
    transition_root = tmp_path / "transition"
    points_root.mkdir()
    output_root.mkdir()
    transition_root.mkdir()
    kwargs = {
        "feature_history_run_spec_path": (tmp_path / "feature-history-spec.json").resolve(),
        "feature_history_run_root": (tmp_path / "feature-history-run").resolve(),
        "audited_development_universe_sqlite_path": (
            tmp_path / "development" / "metadata.sqlite3"
        ).resolve(),
        "expected_development_coverage_audit_sha256": _sha("development:coverage"),
        "expected_development_artifact_root_sha256": _sha("development:artifact"),
        "expected_development_temporal_contract_sha256": _sha("development:temporal"),
        "expected_development_temporal_role": "development",
        "points_output_root": points_root,
        "collection_set_refs": [_ref(session) for session in [*prewindow, *development]],
        "security_code_transition_evidence_root": transition_root,
        "expected_security_code_transition_contract_sha256": _sha("transition-contract"),
        "output_root": output_root,
    }
    return kwargs, prewindow, development


def _verify_kwargs(kwargs: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any]:
    return {
        **kwargs,
        "publication": publication,
    }


def test_v2_publisher_and_capability_free_verifier_rebuild_exact_733_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, prewindow, development = _kwargs(tmp_path, monkeypatch)

    publication = authority.publish_factor_v3_daily_basic_733_exact_set_coverage(**kwargs)
    verified = authority.verify_factor_v3_daily_basic_733_exact_set_coverage(
        **_verify_kwargs(kwargs, publication)
    )

    assert verified["verified"] is True
    assert verified["trade_date_count"] == 733
    assert verified["trade_dates"] == [*prewindow, *development]
    assert "publication_capability" not in publication


def test_v2_rejects_483_only_refs_and_legacy_collection_sets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _prewindow, development = _kwargs(tmp_path, monkeypatch)
    kwargs["collection_set_refs"] = [_ref(session) for session in development]
    with pytest.raises(ValueError, match="733"):
        authority.publish_factor_v3_daily_basic_733_exact_set_coverage(**kwargs)

    kwargs, prewindow, development = _kwargs(tmp_path / "second", monkeypatch)
    refs = [_ref(session) for session in [*prewindow, *development]]
    refs[0] = _ref(prewindow[0], legacy_path=True)
    kwargs["collection_set_refs"] = refs
    with pytest.raises(ValueError, match="daily_basic-only"):
        authority.publish_factor_v3_daily_basic_733_exact_set_coverage(**kwargs)


@pytest.mark.parametrize("artifact", ["receipt", "attestation", "publication"])
def test_capability_free_verifier_rejects_deleted_or_tampered_terminal_artifacts(
    artifact: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _prewindow, _development = _kwargs(tmp_path, monkeypatch)
    publication = authority.publish_factor_v3_daily_basic_733_exact_set_coverage(**kwargs)
    relative_path = publication[f"{artifact}_relative_path"]
    path = Path(kwargs["output_root"]) / Path(*relative_path.split("/"))
    if artifact == "attestation":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ValueError):
        authority.verify_factor_v3_daily_basic_733_exact_set_coverage(
            **_verify_kwargs(kwargs, publication)
        )
