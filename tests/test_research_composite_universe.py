"""Fail-closed composition of independently audited PIT universe segments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest

from app.research_composite_universe import (
    CompositeAuditedUniverse,
    CompositeUniverseError,
    load_composite_universe_descriptor,
    rebase_segmented_causal_bars,
)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class _FakeUniverse:
    label: str
    start_date: str
    end_date: str
    artifact_root_sha256: str
    coverage_audit_sha256: str
    temporal_contract_sha256: str
    temporal_role: str = "development"
    bars: list[dict[str, Any]] | None = None
    opened: bool = True

    external_temporal_authority_verified: ClassVar[bool] = True
    final_oos_eligible: ClassVar[bool] = False
    is_audited_store_artifact: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.manifest = {
            "manifest_sha256": _hash({"manifest": self.label}),
            "market_generations": {"root_sha256": _hash({"market": self.label})},
            "stock_generation": {"lineage_sha256": _hash({"stock": self.label})},
        }
        self.calendar_sha256 = _hash({"calendar": self.label})
        self.source_manifest_sha256 = _hash({"source": self.label})
        self.seed_rows = [{"symbol": self.label, "name": self.label}]
        self.calls: list[tuple[Any, ...]] = []

    def items_as_of(self, signal_date: str) -> list[dict[str, Any]]:
        self.calls.append(("items_as_of", signal_date))
        return [{"symbol": self.label, "trade_date": signal_date}]

    def item_as_of(self, symbol: str, signal_date: str) -> dict[str, Any]:
        self.calls.append(("item_as_of", symbol, signal_date))
        return {"symbol": symbol, "segment": self.label, "trade_date": signal_date}

    def next_open_execution_evidence(
        self, symbol: str, trade_date: str, side: str
    ) -> dict[str, Any]:
        self.calls.append(("next_open", symbol, trade_date, side))
        return {
            "fillable": True,
            "raw_price": 10.0,
            "segment": self.label,
            "generation_proof": {"trade_date": trade_date},
        }

    def open_sessions(self, start_date: str, end_date: str) -> list[str]:
        self.calls.append(("open_sessions", start_date, end_date))
        return [start_date] if start_date == end_date else [start_date, end_date]

    def seed_items(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        self.calls.append(("seed_items", start_date, end_date))
        return [dict(row) for row in self.seed_rows]

    def causal_signal_bars(
        self, symbol: str, start_date: str, as_of_date: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("causal_signal_bars", symbol, start_date, as_of_date))
        return [
            dict(row)
            for row in self.bars or []
            if start_date <= row["trade_date"] <= as_of_date
        ]

    def _require_open(self) -> "_FakeUniverse":
        self.calls.append(("_require_open",))
        if not self.opened:
            raise CompositeUniverseError(f"segment is closed: {self.label}")
        return self

    def close(self) -> None:
        self.opened = False
        self.calls.append(("close",))


def _segment(
    label: str,
    start: str,
    end: str,
    *,
    root_character: str,
    contract: str = "c" * 64,
    role: str = "development",
) -> _FakeUniverse:
    return _FakeUniverse(
        label=label,
        start_date=start,
        end_date=end,
        artifact_root_sha256=root_character * 64,
        coverage_audit_sha256=root_character.upper() * 64,
        temporal_contract_sha256=contract,
        temporal_role=role,
    )


def _annual_segments() -> tuple[_FakeUniverse, _FakeUniverse]:
    return (
        _segment("2022", "2022-01-04", "2022-12-30", root_character="a"),
        _segment("2023", "2023-01-03", "2023-12-29", root_character="b"),
    )


def _bar(
    trade_date: str,
    *,
    raw_open: float,
    bar_factor: float,
    local_asof_factor: float,
    local_adjustment_date: str,
    segment: str,
) -> dict[str, Any]:
    return {
        "ts_code": "600001.SH",
        "trade_date": trade_date,
        "raw_open": raw_open,
        "raw_high": raw_open * 1.1,
        "raw_low": raw_open * 0.9,
        "raw_close": raw_open * 1.05,
        "signal_open": raw_open * bar_factor / local_asof_factor,
        "signal_high": raw_open * 1.1 * bar_factor / local_asof_factor,
        "signal_low": raw_open * 0.9 * bar_factor / local_asof_factor,
        "signal_close": raw_open * 1.05 * bar_factor / local_asof_factor,
        "bar_adj_factor": bar_factor,
        "as_of_adj_factor": local_asof_factor,
        "adjustment_as_of_date": local_adjustment_date,
        "volume_shares": 1_000_000.0,
        "amount_yuan": raw_open * 1_000_000.0,
        "raw_pct_chg": 1.0,
        "raw_pre_close": raw_open / 1.01,
        "raw_volume_lots": 10_000.0,
        "raw_amount_thousand_yuan": raw_open * 1_000.0,
        "generation_proof": {"trade_date": trade_date, "segment": segment},
    }


def test_composite_authority_binds_ordered_segment_authorities_and_gap_policy():
    first, second = _annual_segments()
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    authority = composite.composite_authority
    unsigned = {key: value for key, value in authority.items() if key != "composite_root_sha256"}

    assert authority["schema_version"] == "research-composite-universe/v1"
    assert authority["coverage"] == {
        "start_date": "2022-01-04",
        "end_date": "2023-12-29",
    }
    assert [row["artifact_root_sha256"] for row in authority["segments"]] == [
        "a" * 64,
        "b" * 64,
    ]
    assert authority["permitted_boundary_gaps"] == [
        {"previous_end_date": "2022-12-30", "next_start_date": "2023-01-03"}
    ]
    assert composite.composite_root_sha256 == _hash(unsigned)
    assert authority["composite_root_sha256"] == composite.composite_root_sha256


def test_composite_content_hash_properties_bind_ordered_segment_hashes():
    first, second = _annual_segments()
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    assert composite.universe_sha256 == composite.composite_root_sha256
    assert composite.calendar_sha256 == _hash(
        {
            "schema_version": "research-composite-calendar/v1",
            "segments": [
                {
                    "artifact_root_sha256": first.artifact_root_sha256,
                    "calendar_sha256": first.calendar_sha256,
                },
                {
                    "artifact_root_sha256": second.artifact_root_sha256,
                    "calendar_sha256": second.calendar_sha256,
                },
            ],
        }
    )
    assert composite.source_manifest_sha256 == _hash(
        {
            "schema_version": "research-composite-source-manifest/v1",
            "segments": [
                {
                    "artifact_root_sha256": first.artifact_root_sha256,
                    "source_manifest_sha256": first.source_manifest_sha256,
                },
                {
                    "artifact_root_sha256": second.artifact_root_sha256,
                    "source_manifest_sha256": second.source_manifest_sha256,
                },
            ],
        }
    )
    assert composite.coverage_audit_sha256 == _hash(
        {
            "schema_version": "research-composite-coverage-audit/v1",
            "segments": [
                {
                    "artifact_root_sha256": first.artifact_root_sha256,
                    "coverage_audit_sha256": first.coverage_audit_sha256,
                },
                {
                    "artifact_root_sha256": second.artifact_root_sha256,
                    "coverage_audit_sha256": second.coverage_audit_sha256,
                },
            ],
        }
    )


@pytest.mark.parametrize(
    ("first_range", "second_range", "message"),
    [
        (("2022-01-04", "2022-12-30"), ("2022-12-30", "2023-12-29"), "overlap"),
        (("2022-01-04", "2022-12-30"), ("2023-01-03", "2023-12-29"), "gap"),
    ],
)
def test_composite_rejects_overlap_and_unattested_calendar_gap(
    first_range: tuple[str, str], second_range: tuple[str, str], message: str
):
    first = _segment("first", *first_range, root_character="a")
    second = _segment("second", *second_range, root_character="b")

    with pytest.raises(CompositeUniverseError, match=message):
        CompositeAuditedUniverse([first, second])


def test_composite_rejects_out_of_order_segments():
    first, second = _annual_segments()

    with pytest.raises(CompositeUniverseError, match="ordered"):
        CompositeAuditedUniverse(
            [second, first],
            permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
        )


def test_composite_rejects_role_or_temporal_contract_mismatch():
    first, second = _annual_segments()
    second.temporal_role = "contaminated_diagnostic"
    with pytest.raises(CompositeUniverseError, match="temporal role"):
        CompositeAuditedUniverse(
            [first, second],
            permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
        )

    first, second = _annual_segments()
    second.temporal_contract_sha256 = "d" * 64
    with pytest.raises(CompositeUniverseError, match="temporal contract"):
        CompositeAuditedUniverse(
            [first, second],
            permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
        )


def test_composite_rejects_unverified_or_promotion_eligible_segment():
    first, second = _annual_segments()
    first.external_temporal_authority_verified = False
    with pytest.raises(CompositeUniverseError, match="external temporal authority"):
        CompositeAuditedUniverse(
            [first, second],
            permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
        )

    first, second = _annual_segments()
    second.final_oos_eligible = True
    with pytest.raises(CompositeUniverseError, match="final-OOS"):
        CompositeAuditedUniverse(
            [first, second],
            permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
        )


def test_date_scoped_reads_route_to_exact_owning_segment():
    first, second = _annual_segments()
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    assert composite.items_as_of("2022-06-01")[0]["symbol"] == "2022"
    assert composite.item_as_of("600001", "2023-06-01")["segment"] == "2023"
    assert composite.next_open_execution_evidence(
        "600001", "2023-01-03", "buy"
    )["segment"] == "2023"
    assert first.calls == [("items_as_of", "2022-06-01")]
    assert second.calls == [
        ("item_as_of", "600001", "2023-06-01"),
        ("next_open", "600001", "2023-01-03", "buy"),
    ]

    with pytest.raises(CompositeUniverseError, match="not owned"):
        composite.items_as_of("2023-01-02")


def test_composite_require_open_checks_every_segment_and_exposes_verified_authority():
    first, second = _annual_segments()
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    assert composite._require_open() is composite
    assert composite.external_temporal_authority_verified is True
    assert composite.artifact_root_sha256 == composite.composite_root_sha256
    assert first.calls == [("_require_open",)]
    assert second.calls == [("_require_open",)]

    second.opened = False
    with pytest.raises(CompositeUniverseError, match="closed"):
        composite._require_open()


def test_composite_open_sessions_splits_range_and_keeps_global_order():
    first, second = _annual_segments()
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    assert composite.open_sessions("2022-12-29", "2023-01-04") == [
        "2022-12-29",
        "2022-12-30",
        "2023-01-03",
        "2023-01-04",
    ]
    assert first.calls == [("open_sessions", "2022-12-29", "2022-12-30")]
    assert second.calls == [("open_sessions", "2023-01-03", "2023-01-04")]


def test_composite_seed_items_unions_symbols_in_first_seen_order_and_close_closes_all():
    first, second = _annual_segments()
    first.seed_rows = [
        {"symbol": "600001", "name": "first-name"},
        {"symbol": "600002", "name": "only-first"},
    ]
    second.seed_rows = [
        {"symbol": "600001", "name": "later-name"},
        {"symbol": "600003", "name": "only-second"},
    ]
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    assert composite.seed_items("2022-12-29", "2023-01-04") == [
        {"symbol": "600001", "name": "first-name"},
        {"symbol": "600002", "name": "only-first"},
        {"symbol": "600003", "name": "only-second"},
    ]
    composite.close()
    assert first.opened is False
    assert second.opened is False
    assert first.calls[-1] == ("close",)
    assert second.calls[-1] == ("close",)


def _descriptor(first: _FakeUniverse, second: _FakeUniverse) -> dict[str, Any]:
    def segment(path: str, universe: _FakeUniverse) -> dict[str, Any]:
        return {
            "path": path,
            "expected_artifact_root_sha256": universe.artifact_root_sha256,
            "expected_coverage_audit_sha256": universe.coverage_audit_sha256,
            "expected_temporal_contract_sha256": universe.temporal_contract_sha256,
            "expected_artifact_manifest_sha256": universe.manifest["manifest_sha256"],
            "expected_temporal_role": universe.temporal_role,
        }

    return {
        "schema_version": "research-composite-universe-descriptor/v1",
        "segments": [segment("first.sqlite3", first), segment("second.sqlite3", second)],
        "permitted_boundary_gaps": [
            {"previous_end_date": "2022-12-30", "next_start_date": "2023-01-03"}
        ],
    }


def test_descriptor_loader_opens_every_segment_with_external_anchors(
    tmp_path, monkeypatch
):
    first, second = _annual_segments()
    descriptor_path = tmp_path / "composite.json"
    descriptor_path.write_text(json.dumps(_descriptor(first, second)), encoding="utf-8")
    expected = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    ).composite_root_sha256
    opened = {
        str((tmp_path / "first.sqlite3").resolve()): first,
        str((tmp_path / "second.sqlite3").resolve()): second,
    }
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_from_file(path: str, **kwargs: Any) -> _FakeUniverse:
        calls.append((path, kwargs))
        return opened[path]

    monkeypatch.setattr(
        "app.research_pit_store.AuditedPointInTimeUniverse.from_file", fake_from_file
    )

    composite = load_composite_universe_descriptor(
        str(descriptor_path), expected_composite_root_sha256=expected
    )

    assert composite.composite_root_sha256 == expected
    assert [path for path, _kwargs in calls] == list(opened)
    assert calls[0][1] == {
        "expected_coverage_audit_sha256": first.coverage_audit_sha256,
        "expected_artifact_root_sha256": first.artifact_root_sha256,
        "expected_temporal_contract_sha256": first.temporal_contract_sha256,
        "expected_temporal_role": "development",
    }


def test_descriptor_loader_closes_opened_segments_on_composite_root_mismatch(
    tmp_path, monkeypatch
):
    first, second = _annual_segments()
    descriptor_path = tmp_path / "composite.json"
    descriptor_path.write_text(json.dumps(_descriptor(first, second)), encoding="utf-8")
    opened = iter([first, second])
    monkeypatch.setattr(
        "app.research_pit_store.AuditedPointInTimeUniverse.from_file",
        lambda *args, **kwargs: next(opened),
    )

    with pytest.raises(CompositeUniverseError, match="composite root"):
        load_composite_universe_descriptor(
            str(descriptor_path), expected_composite_root_sha256="f" * 64
        )

    assert first.opened is False
    assert second.opened is False


def test_composite_causal_signal_bars_rebases_all_segments_to_global_asof():
    first, second = _annual_segments()
    first.bars = [
        _bar(
            "2022-12-29",
            raw_open=10.0,
            bar_factor=1.0,
            local_asof_factor=1.0,
            local_adjustment_date="2022-12-30",
            segment="2022",
        )
    ]
    second.bars = [
        _bar(
            "2023-06-01",
            raw_open=6.0,
            bar_factor=2.0,
            local_asof_factor=2.0,
            local_adjustment_date="2023-06-01",
            segment="2023",
        )
    ]
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    rows = composite.causal_signal_bars("600001", "2022-12-29", "2023-06-01")

    assert [row["trade_date"] for row in rows] == ["2022-12-29", "2023-06-01"]
    assert rows[0]["signal_open"] == pytest.approx(5.0)
    assert rows[1]["signal_open"] == pytest.approx(6.0)
    assert all(row["as_of_adj_factor"] == pytest.approx(2.0) for row in rows)
    assert all(row["adjustment_as_of_date"] == "2023-06-01" for row in rows)
    assert first.calls == [
        ("causal_signal_bars", "600001", "2022-12-29", "2022-12-30")
    ]
    assert second.calls == [
        ("causal_signal_bars", "600001", "2023-01-03", "2023-06-01")
    ]


def test_rebase_segmented_causal_bars_uses_one_global_asof_factor():
    first = [
        {
            "ts_code": "600001.SH",
            "trade_date": "2022-12-29",
            "raw_open": 10.0,
            "raw_high": 11.0,
            "raw_low": 9.0,
            "raw_close": 10.5,
            "signal_open": 10.0,
            "signal_high": 11.0,
            "signal_low": 9.0,
            "signal_close": 10.5,
            "bar_adj_factor": 1.0,
            "as_of_adj_factor": 1.0,
            "adjustment_as_of_date": "2022-12-30",
            "generation_proof": {"trade_date": "2022-12-29", "segment": "2022"},
        }
    ]
    second = [
        {
            "ts_code": "600001.SH",
            "trade_date": "2023-06-01",
            "raw_open": 6.0,
            "raw_high": 6.6,
            "raw_low": 5.4,
            "raw_close": 6.3,
            "signal_open": 6.0,
            "signal_high": 6.6,
            "signal_low": 5.4,
            "signal_close": 6.3,
            "bar_adj_factor": 2.0,
            "as_of_adj_factor": 2.0,
            "adjustment_as_of_date": "2023-06-01",
            "generation_proof": {"trade_date": "2023-06-01", "segment": "2023"},
        }
    ]

    rows = rebase_segmented_causal_bars([first, second], as_of_date="2023-06-01")

    assert [row["trade_date"] for row in rows] == ["2022-12-29", "2023-06-01"]
    assert rows[0]["signal_open"] == pytest.approx(5.0)
    assert rows[0]["signal_high"] == pytest.approx(5.5)
    assert rows[0]["signal_low"] == pytest.approx(4.5)
    assert rows[0]["signal_close"] == pytest.approx(5.25)
    assert rows[1]["signal_open"] == pytest.approx(6.0)
    assert all(row["as_of_adj_factor"] == pytest.approx(2.0) for row in rows)
    assert all(row["adjustment_as_of_date"] == "2023-06-01" for row in rows)
    assert rows[0]["generation_proof"]["segment"] == "2022"


def test_rebase_segmented_causal_bars_rejects_duplicate_dates_and_mixed_symbols():
    row = {
        "ts_code": "600001.SH",
        "trade_date": "2023-01-03",
        "raw_open": 10.0,
        "raw_high": 11.0,
        "raw_low": 9.0,
        "raw_close": 10.0,
        "bar_adj_factor": 1.0,
        "as_of_adj_factor": 1.0,
        "adjustment_as_of_date": "2023-01-03",
    }
    with pytest.raises(CompositeUniverseError, match="duplicate"):
        rebase_segmented_causal_bars([[row], [dict(row)]], as_of_date="2023-01-03")

    other = {**row, "ts_code": "000001.SZ", "trade_date": "2023-01-04"}
    with pytest.raises(CompositeUniverseError, match="one symbol"):
        rebase_segmented_causal_bars([[row], [other]], as_of_date="2023-01-04")
