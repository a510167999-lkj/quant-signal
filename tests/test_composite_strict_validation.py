import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.artifact_native_evidence import (
    build_artifact_native_evidence,
    verify_artifact_native_evidence,
    write_artifact_native_evidence,
)
from app.research_composite_universe import CompositeAuditedUniverse
from app.research_pit import (
    write_strict_qualified_trades_payload,
    write_strict_research_evidence_bundle,
)
from app.research_validation import (
    audited_authority_from_universe,
    qualified_trades_sha256,
    verify_artifact_trade_lineage,
)
from app.research_scope import is_mainboard_chinext_item, market_scope_contract


class _Segment:
    is_audited_store_artifact = True
    external_temporal_authority_verified = True
    final_oos_eligible = False
    temporal_role = "development"
    temporal_contract_sha256 = "c" * 64

    def __init__(
        self,
        label: str,
        start_date: str,
        end_date: str,
        *,
        exchange: str = "SSE",
        list_date: str = "2010-01-01",
    ) -> None:
        marker = label[0]
        self.start_date = start_date
        self.end_date = end_date
        self.artifact_root_sha256 = marker * 64
        self.coverage_audit_sha256 = marker.upper() * 64
        self.calendar_sha256 = ("1" if label == "a" else "2") * 64
        self.source_manifest_sha256 = ("3" if label == "a" else "4") * 64
        self.manifest = {
            "manifest_sha256": ("5" if label == "a" else "6") * 64,
            "coverage": {"start_date": start_date, "end_date": end_date},
            "market_generations": {
                "root_sha256": ("7" if label == "a" else "8") * 64,
            },
            "stock_generation": {
                "lineage_sha256": ("9" if label == "a" else "0") * 64,
            },
        }
        self.closed = False
        self.exchange = exchange
        self.list_date = list_date

    @property
    def universe_sha256(self) -> str:
        return self.artifact_root_sha256

    def _require_open(self):
        if self.closed:
            raise ValueError("segment is closed")
        return self

    def item_as_of(self, symbol, signal_date):
        normalized = str(symbol)
        suffix = "SH" if self.exchange == "SSE" else "SZ"
        return {
            "symbol": normalized,
            "ts_code": f"{normalized}.{suffix}",
            "exchange": self.exchange,
            "list_date": self.list_date,
            "market": "a",
            "name": "synthetic",
            "industry": "synthetic",
        }

    def items_as_of(self, signal_date):
        return [self.item_as_of("600001", signal_date)]

    def open_sessions(self, start_date, end_date):
        known = [
            "2019-12-31",
            "2020-01-01",
            "2020-01-02",
            "2020-01-03",
            "2020-01-04",
        ]
        return [day for day in known if str(start_date) <= day <= str(end_date)]

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        day = str(as_of_date)
        return [
            {
                "ts_code": str(symbol),
                "trade_date": day,
                "raw_open": 10.0,
                "raw_high": 10.2,
                "raw_low": 9.8,
                "raw_close": 10.1,
                "bar_adj_factor": 1.0,
                "as_of_adj_factor": 1.0,
                "adjustment_as_of_date": day,
                "signal_open": 10.0,
                "signal_high": 10.2,
                "signal_low": 9.8,
                "signal_close": 10.1,
                "volume_shares": 1_000_000,
                "generation_proof": {"trade_date": day},
            }
        ]

    def next_open_execution_evidence(self, symbol, trade_date, side):
        return {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 10.0 if side == "buy" else 11.0,
            "generation_proof": {
                "trade_date": str(trade_date),
                "side": str(side),
                "generation_id": f"g-{trade_date}",
            },
        }

    def seed_items(self, start_date, end_date):
        return [{"symbol": "600001", "name": "synthetic"}]

    def close(self):
        self.closed = True


class _Single(_Segment):
    def __init__(self) -> None:
        super().__init__("a", "2020-01-01", "2020-01-04")


class _DifferentSymbolSegment(_Segment):
    def items_as_of(self, signal_date):
        return [self.item_as_of("000001", signal_date)]


def _composite() -> CompositeAuditedUniverse:
    return CompositeAuditedUniverse(
        [
            _Segment("a", "2019-12-31", "2019-12-31"),
            _Segment("b", "2020-01-01", "2020-01-04"),
        ]
    )


def _trade() -> dict:
    return {
        "symbol": "600001",
        "signal_date": "2020-01-01",
        "entry_date": "2020-01-02",
        "planned_exit_date": "2020-01-04",
        "exit_date": "2020-01-04",
        "holding_days": 2,
        "exit_reason": "time_exit_next_open",
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
        "entry_executability": {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 10.0,
            "generation_proof": {
                "trade_date": "2020-01-02",
                "side": "buy",
                "generation_id": "g-2020-01-02",
            },
        },
        "exit_execution_evidence": {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 11.0,
            "generation_proof": {
                "trade_date": "2020-01-04",
                "side": "sell",
                "generation_id": "g-2020-01-04",
            },
        },
    }


def _write_qualified(root: Path) -> Path:
    path = root / "qualified.json"
    path.write_text(
        json.dumps({"summary": {}, "qualified_trades": [_trade()]}),
        encoding="utf-8",
    )
    return path


def test_authority_keeps_single_shape_but_uses_versioned_composite_envelope():
    single = audited_authority_from_universe(_Single())
    assert set(single) == {
        "artifact_root_sha256",
        "coverage_audit_sha256",
        "temporal_contract_sha256",
        "temporal_role",
        "artifact_manifest_sha256",
        "market_generation_root_sha256",
        "stock_generation_lineage_sha256",
    }

    composite = _composite()
    authority = audited_authority_from_universe(composite)
    assert authority["schema_version"] == "research-composite-audited-authority/v1"
    assert authority["authority_kind"] == "ordered_composite"
    assert authority["composite_root_sha256"] == composite.composite_root_sha256
    assert authority["composite_authority"]["coverage"] == {
        "start_date": "2019-12-31",
        "end_date": "2020-01-04",
    }
    assert authority["composite_authority"] == composite.composite_authority


def test_composite_authority_rejects_order_or_root_tamper():
    composite = _composite()
    tampered = deepcopy(composite.composite_authority)
    tampered["segments"] = list(reversed(tampered["segments"]))
    composite._composite_authority = tampered

    with pytest.raises(ValueError, match="composite.*(root|authority|order)"):
        audited_authority_from_universe(composite)


def test_composite_native_evidence_uses_new_schema_and_full_coverage(tmp_path):
    composite = _composite()
    qualified = _write_qualified(tmp_path)
    payload = build_artifact_native_evidence(
        audited_universe=composite,
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified),
        artifact_root=str(tmp_path),
    )

    assert payload["schema_version"] == "research_artifact_native_evidence/v7"
    assert (
        payload["producer_code_binding"]["schema_version"]
        == "artifact_native_producer_code_binding/v3"
    )
    assert "outcome_contract_module" in payload["producer_code_binding"][
        "components"
    ]
    assert payload["coverage"] == {
        "start_date": "2019-12-31",
        "end_date": "2020-01-04",
    }
    assert payload["authority"]["authority_kind"] == "ordered_composite"
    assert (
        payload["authority"]["composite_root_sha256"]
        == composite.composite_root_sha256
    )
    continuity = payload["cross_segment_identity_continuity"]
    assert continuity["schema_version"] == "cross_segment_identity_continuity/v2"
    assert continuity["boundary_count"] == 1
    assert continuity["shared_identity_count"] == 1
    assert continuity["bound"] is True


def test_composite_native_evidence_refuses_unexplained_membership_transition(
    tmp_path,
):
    composite = CompositeAuditedUniverse(
        [
            _Segment("a", "2019-12-31", "2019-12-31"),
            _DifferentSymbolSegment("b", "2020-01-01", "2020-01-04"),
        ]
    )
    qualified = _write_qualified(tmp_path)
    payload = build_artifact_native_evidence(
        audited_universe=composite,
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified),
        artifact_root=str(tmp_path),
    )

    continuity = payload["cross_segment_identity_continuity"]
    assert continuity["shared_identity_count"] == 0
    assert continuity["unexplained_transition_count"] == 2
    assert continuity["bound"] is False
    assert "cross_segment_identity_continuity_not_bound" in payload[
        "eligibility"
    ]["reasons"]


def test_composite_native_evidence_rejects_cross_segment_identity_conflict(tmp_path):
    composite = CompositeAuditedUniverse(
        [
            _Segment("a", "2019-12-31", "2019-12-31", exchange="SSE"),
            _Segment("b", "2020-01-01", "2020-01-04", exchange="SZSE"),
        ]
    )
    qualified = _write_qualified(tmp_path)

    with pytest.raises(ValueError, match="cross-segment identity conflict"):
        build_artifact_native_evidence(
            audited_universe=composite,
            qualified_trades=[_trade()],
            qualified_trades_path=str(qualified),
            artifact_root=str(tmp_path),
        )


def test_composite_native_verifier_replays_identity_continuity(tmp_path):
    composite = _composite()
    qualified = _write_qualified(tmp_path)
    payload = build_artifact_native_evidence(
        audited_universe=composite,
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified),
        artifact_root=str(tmp_path),
    )
    written = write_artifact_native_evidence(str(tmp_path), payload)
    composite.verified_segments[1].list_date = "2011-01-01"

    with pytest.raises(ValueError, match="cross-segment identity conflict"):
        verify_artifact_native_evidence(
            written["path"],
            audited_universe=composite,
            artifact_root=str(tmp_path),
        )


def _eligible_native_payload(source_path: Path) -> dict:
    return {
        "schema_version": "research_artifact_native_evidence/v7",
        "qualified_trades": {
            "path": source_path.name,
            "qualified_trades_sha256": qualified_trades_sha256([_trade()]),
        },
        "trade_lineage_sha256": "b" * 64,
        "market_scope": {**market_scope_contract(), "verified_trade_count": 1},
        "eligibility": {
            "eligible_for_development_validation": True,
            "eligible_for_final_validation": False,
            "final_oos_eligible": False,
            "reasons": [],
        },
    }


def test_composite_strict_bundle_and_data_contract_are_versioned(
    tmp_path, monkeypatch
):
    composite = _composite()
    qualified = _write_qualified(tmp_path)
    native_path = tmp_path / "native.json"
    native_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "app.artifact_native_evidence.verify_artifact_native_evidence",
        lambda *args, **kwargs: _eligible_native_payload(qualified),
    )

    strict = write_strict_research_evidence_bundle(
        str(tmp_path),
        audited_universe=composite,
        artifact_native_evidence_path=str(native_path),
    )
    strict_payload = json.loads(Path(strict["path"]).read_text(encoding="utf-8"))
    assert strict_payload["schema_version"] == "research_pit_evidence_bundle/v7"
    assert (
        strict_payload["audited_authority"]["authority_kind"]
        == "ordered_composite"
    )
    assert strict_payload["coverage"] == {
        "start_date": "2019-12-31",
        "end_date": "2020-01-04",
    }

    verified = {
        "qualified_trades_sha256": qualified_trades_sha256([_trade()]),
        "qualified_trade_lineage_sha256": "b" * 64,
        "universe_sha256": composite.universe_sha256,
        "calendar_sha256": composite.calendar_sha256,
        "source_manifest_sha256": composite.source_manifest_sha256,
        "evidence_bundle_sha256": strict["evidence_bundle_sha256"],
        "audited_authority": strict_payload["audited_authority"],
        "artifact_native_evidence": _eligible_native_payload(qualified),
    }
    monkeypatch.setattr(
        "app.research_pit.verify_research_evidence_bundle",
        lambda *args, **kwargs: verified,
    )
    monkeypatch.setattr(
        "app.research_validation.validate_point_in_time_contract",
        lambda *args, **kwargs: {"eligible_for_development_validation": True},
    )
    compiled = write_strict_qualified_trades_payload(
        str(tmp_path),
        source_payload_path=str(qualified),
        strict_evidence_bundle_path=strict["path"],
        audited_universe=composite,
    )
    compiled_payload = json.loads(Path(compiled["path"]).read_text(encoding="utf-8"))
    contract = compiled_payload["summary"]["research_data_contract"]
    assert contract["schema_version"] == "research_data_contract/v4"
    assert contract["audited_authority"]["authority_kind"] == "ordered_composite"
    assert contract["artifact_root_sha256"] == composite.composite_root_sha256


def test_single_native_v2_strict_v3_and_contract_v1_do_not_regress(
    tmp_path, monkeypatch
):
    single = _Single()
    qualified = _write_qualified(tmp_path)
    native = build_artifact_native_evidence(
        audited_universe=single,
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified),
        artifact_root=str(tmp_path),
    )
    assert native["schema_version"] == "research_artifact_native_evidence/v4"

    native_path = tmp_path / "single-native.json"
    native_path.write_text("{}", encoding="utf-8")
    eligible_native = _eligible_native_payload(qualified)
    eligible_native["schema_version"] = "research_artifact_native_evidence/v4"
    monkeypatch.setattr(
        "app.artifact_native_evidence.verify_artifact_native_evidence",
        lambda *args, **kwargs: eligible_native,
    )
    strict = write_strict_research_evidence_bundle(
        str(tmp_path),
        audited_universe=single,
        artifact_native_evidence_path=str(native_path),
    )
    strict_payload = json.loads(Path(strict["path"]).read_text(encoding="utf-8"))
    assert strict_payload["schema_version"] == "research_pit_evidence_bundle/v5"

    authority = audited_authority_from_universe(single)
    verified = {
        "qualified_trades_sha256": qualified_trades_sha256([_trade()]),
        "qualified_trade_lineage_sha256": "b" * 64,
        "universe_sha256": single.universe_sha256,
        "calendar_sha256": single.calendar_sha256,
        "source_manifest_sha256": single.source_manifest_sha256,
        "evidence_bundle_sha256": strict["evidence_bundle_sha256"],
        "audited_authority": authority,
        "artifact_native_evidence": eligible_native,
    }
    monkeypatch.setattr(
        "app.research_pit.verify_research_evidence_bundle",
        lambda *args, **kwargs: verified,
    )
    monkeypatch.setattr(
        "app.research_validation.validate_point_in_time_contract",
        lambda *args, **kwargs: {"eligible_for_development_validation": True},
    )
    compiled = write_strict_qualified_trades_payload(
        str(tmp_path),
        source_payload_path=str(qualified),
        strict_evidence_bundle_path=strict["path"],
        audited_universe=single,
    )
    compiled_payload = json.loads(Path(compiled["path"]).read_text(encoding="utf-8"))
    assert (
        compiled_payload["summary"]["research_data_contract"]["schema_version"]
        == "research_data_contract/v2"
    )


@pytest.mark.parametrize(
    "symbol",
    ["000001.SZ", "002001.SZ", "300001.SZ", "302132.SZ", "600001.SH", "605001.SH"],
)
def test_research_scope_accepts_only_mainboard_and_chinext_prefixes(symbol):
    assert is_mainboard_chinext_item({"ts_code": symbol}) is True


@pytest.mark.parametrize("symbol", ["688001.SH", "689001.SH", "430001.BJ", "920001.BJ"])
def test_research_scope_rejects_star_and_beijing_prefixes(symbol):
    assert is_mainboard_chinext_item({"ts_code": symbol}) is False


def test_trade_lineage_distinguishes_planned_and_actual_holding_sessions():
    trade = _trade()
    trade.update(
        planned_exit_date="2020-01-03",
        holding_days=2,
        planned_holding_sessions=1,
        actual_holding_sessions=2,
    )
    assert len(verify_artifact_trade_lineage(_Single(), [trade])) == 64


def test_trade_lineage_rejects_noncanonical_date_suffix():
    trade = _trade()
    trade["signal_date"] = "2020-01-01-extra"
    with pytest.raises(ValueError, match="canonical ISO date"):
        verify_artifact_trade_lineage(_Single(), [trade])


def test_trade_lineage_rejects_out_of_scope_symbol_before_artifact_lookup():
    trade = _trade()
    trade["symbol"] = "688001.SH"
    with pytest.raises(ValueError, match="outside the frozen"):
        verify_artifact_trade_lineage(_Single(), [trade])
