import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app import research_backtest
from app import jobs
from app.indicators import add_indicators
from app.research_backtest import (
    _batch_pit_eligible_dates_by_symbol,
    _build_historical_candidate_maps,
    _pit_historical_member,
    _resolve_historical_universe,
    run_historical_universe_research_backtest,
)
from app.research_context import _historical_market_breadth
from app.signals import evaluate_signal
from app.strategy_signal_evidence import build_signal_snapshot
from app.research_pit import (
    PointInTimeUniverse,
    build_pit_universe_payload,
    verify_research_evidence_bundle,
    write_frozen_market_data_manifest,
    write_pit_universe_artifact,
    write_research_evidence_bundle,
)


def _master():
    return [
        {
            "ts_code": "600001.SH",
            "symbol": "600001",
            "name": "当前名称不得用于历史筛选",
            "exchange": "SSE",
            "list_status": "D",
            "list_date": "20100101",
            "delist_date": "20240104",
        },
        {
            "ts_code": "000002.SZ",
            "symbol": "000002",
            "name": "未来上市",
            "exchange": "SZSE",
            "list_status": "L",
            "list_date": "20240103",
            "delist_date": None,
        },
    ]


def _calendar():
    return [
        {"cal_date": "20240102", "is_open": 1},
        {"cal_date": "20240103", "is_open": 1},
        {"cal_date": "20240104", "is_open": 1},
    ]


def _daily_universe():
    return [
        {
            "trade_date": "20240102",
            "ts_code": "600001.SH",
            "name": "历史正常名称",
            "industry": "银行",
        },
        {
            "trade_date": "20240103",
            "ts_code": "600001.SH",
            "name": "历史ST名称",
            "industry": "银行",
        },
        {
            "trade_date": "20240103",
            "ts_code": "000002.SZ",
            "name": "上市首日名称",
            "industry": "地产",
        },
        {
            "trade_date": "20240104",
            "ts_code": "000002.SZ",
            "name": "上市后名称",
            "industry": "地产",
        },
    ]


def _payload(**overrides):
    values = {
        "security_master": _master(),
        "trade_calendar": _calendar(),
        "daily_universe": _daily_universe(),
        "source_manifest": {
            "provider": "synthetic-test-source",
            "stock_basic_raw_sha256": "1" * 64,
            "bak_basic_raw_sha256": "2" * 64,
            "trade_cal_raw_sha256": "3" * 64,
        },
        "start_date": "2024-01-02",
        "end_date": "2024-01-04",
    }
    values.update(overrides)
    return build_pit_universe_payload(**values)


def test_pit_universe_uses_exact_historical_membership_and_name():
    universe = PointInTimeUniverse.from_payload(_payload())

    assert universe.item_as_of("600001", "2024-01-02")["name"] == "历史正常名称"
    assert universe.item_as_of("000002", "2024-01-02") is None
    assert universe.item_as_of("600001", "2024-01-03")["name"] == "历史ST名称"
    assert universe.item_as_of("600001", "2024-01-04") is None
    assert {item["symbol"] for item in universe.seed_items("2024-01-02", "2024-01-04")} == {
        "000002",
        "600001",
    }


def test_pit_universe_items_as_of_returns_sorted_snapshot_copies():
    universe = PointInTimeUniverse.from_payload(_payload())

    rows = universe.items_as_of("2024-01-03")

    assert [row["symbol"] for row in rows] == ["000002", "600001"]
    assert rows == [
        universe.item_as_of("000002", "2024-01-03"),
        universe.item_as_of("600001", "2024-01-03"),
    ]
    rows[0]["name"] = "caller mutation"
    assert universe.items_as_of("2024-01-03")[0]["name"] == "上市首日名称"


def test_pit_universe_items_as_of_tracks_listing_and_delisting_boundaries():
    universe = PointInTimeUniverse.from_payload(_payload())

    assert [row["symbol"] for row in universe.items_as_of("2024-01-02")] == [
        "600001"
    ]
    assert [row["symbol"] for row in universe.items_as_of("2024-01-04")] == [
        "000002"
    ]
    with pytest.raises(ValueError, match="outside PIT universe coverage"):
        universe.items_as_of("2024-01-01")


def test_pit_universe_open_sessions_is_bounded_and_sorted():
    universe = PointInTimeUniverse.from_payload(_payload())

    assert universe.open_sessions("2024-01-02", "2024-01-03") == [
        "2024-01-02",
        "2024-01-03",
    ]
    with pytest.raises(ValueError, match="outside PIT universe coverage"):
        universe.open_sessions("2024-01-01", "2024-01-03")
    with pytest.raises(ValueError, match="end_date precedes start_date"):
        universe.open_sessions("2024-01-03", "2024-01-02")


def test_pit_universe_rejects_missing_session_snapshot():
    rows = [row for row in _daily_universe() if row["trade_date"] != "20240103"]

    with pytest.raises(ValueError, match="missing daily universe snapshot"):
        _payload(daily_universe=rows)


def test_pit_universe_rejects_missing_effective_delist_date():
    master = _master()
    master[0] = {**master[0], "delist_date": None}

    with pytest.raises(ValueError, match="delisted security requires delist_date"):
        _payload(security_master=master)


def test_pit_universe_rejects_symbol_outside_lifecycle():
    rows = _daily_universe() + [
        {
            "trade_date": "20240102",
            "ts_code": "000002.SZ",
            "name": "未来数据",
            "industry": "地产",
        }
    ]

    with pytest.raises(ValueError, match="outside listing lifecycle"):
        _payload(daily_universe=rows)


def test_pit_universe_hashes_are_deterministic_and_tamper_evident(tmp_path):
    first = _payload()
    second = build_pit_universe_payload(
        security_master=list(reversed(_master())),
        trade_calendar=list(reversed(_calendar())),
        daily_universe=list(reversed(_daily_universe())),
        source_manifest=first["source_manifest"],
        start_date="2024-01-02",
        end_date="2024-01-04",
    )
    assert first["hashes"] == second["hashes"]

    descriptor = write_pit_universe_artifact(str(tmp_path), first)
    assert descriptor["filename"] == f'{first["hashes"]["universe_sha256"]}.json'
    artifact_path = tmp_path / descriptor["filename"]
    loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
    loaded["daily_universe"][0]["name"] = "篡改后名称"
    artifact_path.write_text(json.dumps(loaded, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        PointInTimeUniverse.from_file(str(artifact_path))


def _write_verified_evidence(root, audited_authority=None):
    source_dir = root / "universe-source"
    source_dir.mkdir()
    source_files = {}
    for name in ("stock_basic", "bak_basic", "trade_cal"):
        path = source_dir / f"{name}.json"
        path.write_text(json.dumps([{"source": name}]), encoding="utf-8")
        source_files[name] = path

    source_manifest = {
        "provider": "synthetic-test-source",
        "raw_artifacts": {},
    }
    for name, path in source_files.items():
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        source_manifest[f"{name}_raw_sha256"] = digest
        source_manifest["raw_artifacts"][name] = {
            "path": str(path.relative_to(root)),
            "sha256": digest,
            "bytes": len(content),
        }

    universe_payload = _payload(source_manifest=source_manifest)
    universe = write_pit_universe_artifact(str(root), universe_payload)

    market_dir = root / "market"
    market_dir.mkdir()
    components = {}
    for name, rows in {
        "raw_execution_bars": [{"date": "2024-01-02", "symbol": "600001", "open": 10}],
        "corporate_actions": [{"ex_date": "2024-01-03", "symbol": "600001", "ratio": 0}],
        "causal_signal_bars": [{"date": "2024-01-02", "symbol": "600001", "close": 10}],
    }.items():
        path = market_dir / f"{name}.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        components[name] = str(path)
    market = write_frozen_market_data_manifest(
        str(root),
        components=components,
        start_date="2024-01-02",
        end_date="2024-01-04",
        source={"provider": "synthetic-test-market", "knowledge_cutoff": "2024-01-04T15:05:00+08:00"},
    )
    return write_research_evidence_bundle(
        str(root),
        pit_universe_path=universe["path"],
        market_data_manifest_path=market["path"],
        audited_authority=audited_authority,
    )


def test_v2_evidence_bundle_hash_binds_exact_audited_authority(tmp_path):
    authority = {
        "artifact_root_sha256": "1" * 64,
        "coverage_audit_sha256": "2" * 64,
        "temporal_contract_sha256": "3" * 64,
        "temporal_role": "development",
        "artifact_manifest_sha256": "4" * 64,
        "market_generation_root_sha256": "5" * 64,
        "stock_generation_lineage_sha256": "6" * 64,
    }
    evidence = _write_verified_evidence(tmp_path, authority)
    verified = verify_research_evidence_bundle(evidence["path"])
    assert verified["schema_version"] == "research_pit_evidence_bundle/v2"
    assert verified["audited_authority"] == authority
    assert verified["eligibility"]["reasons"]
    assert verified["eligible_for_development_validation"] is False
    assert verified["integrity_only"] is True
    assert verified["eligible_for_final_validation"] is False
    assert verified["final_oos_eligible"] is False


def test_v2_evidence_rejects_claimed_final_eligibility(tmp_path):
    authority = {
        "artifact_root_sha256": "1" * 64,
        "coverage_audit_sha256": "2" * 64,
        "temporal_contract_sha256": "3" * 64,
        "temporal_role": "development",
        "artifact_manifest_sha256": "4" * 64,
        "market_generation_root_sha256": "5" * 64,
        "stock_generation_lineage_sha256": "6" * 64,
    }
    evidence = _write_verified_evidence(tmp_path, authority)
    payload = json.loads(Path(evidence["path"]).read_text(encoding="utf-8"))
    payload["eligibility"]["eligible_for_final_validation"] = True
    semantic = dict(payload)
    semantic.pop("evidence_bundle_sha256")
    payload["evidence_bundle_sha256"] = hashlib.sha256(
        json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    tampered = tmp_path / "tampered-v2.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="eligibility mismatch"):
        verify_research_evidence_bundle(str(tampered))


def test_research_evidence_bundle_rehashes_every_source_component(tmp_path):
    evidence = _write_verified_evidence(tmp_path)

    verified = verify_research_evidence_bundle(evidence["path"])

    assert verified["evidence_bundle_sha256"] == evidence["evidence_bundle_sha256"]
    assert verified["universe_sha256"]
    assert verified["calendar_sha256"]
    assert verified["source_manifest_sha256"]

    raw_bars = tmp_path / "market" / "raw_execution_bars.json"
    raw_bars.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="component (byte count|hash) mismatch"):
        verify_research_evidence_bundle(evidence["path"])


def test_pit_universe_rejects_source_manifest_without_real_raw_hashes():
    with pytest.raises(ValueError, match="source manifest"):
        _payload(source_manifest={"provider": "self-asserted-only"})


def test_historical_backtest_resolver_never_reads_current_snapshot_in_pit_mode(
    tmp_path, monkeypatch
):
    descriptor = write_pit_universe_artifact(str(tmp_path), _payload())
    monkeypatch.setattr(
        research_backtest,
        "_load_research_universe_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("current snapshot read")),
    )

    items, universe = _resolve_historical_universe(
        settings=object(),
        use_live_snapshot=False,
        max_universe_symbols=0,
        start_date="2024-01-02",
        pit_universe_path=descriptor["path"],
    )

    assert universe is not None
    assert {item["symbol"] for item in items} == {"000002", "600001"}


def test_historical_backtest_rejects_current_liquidity_cap_in_pit_mode(tmp_path):
    descriptor = write_pit_universe_artifact(str(tmp_path), _payload())

    with pytest.raises(ValueError, match="max_universe_symbols=0"):
        _resolve_historical_universe(
            settings=object(),
            use_live_snapshot=False,
            max_universe_symbols=300,
            start_date="2024-01-02",
            pit_universe_path=descriptor["path"],
        )


def test_historical_resolver_explicitly_separates_audited_sqlite_from_legacy_input(
    monkeypatch,
):
    calls = []

    class Universe:
        end_date = "2024-01-03"
        final_oos_eligible = False

        def __init__(self, source):
            self.source = source

        def seed_items(self, start_date, end_date):
            return [{"symbol": self.source, "market": "a", "name": self.source}]

    class AuditedLoader:
        @classmethod
        def from_file(cls, path, *, expected_coverage_audit_sha256):
            calls.append(("audited", path, expected_coverage_audit_sha256))
            return Universe("audited")

    class LegacyLoader:
        @classmethod
        def from_file(cls, path):
            calls.append(("legacy", path, None))
            return Universe("legacy")

    monkeypatch.setattr(
        research_backtest,
        "AuditedPointInTimeUniverse",
        AuditedLoader,
        raising=False,
    )
    monkeypatch.setattr(research_backtest, "PointInTimeUniverse", LegacyLoader)

    audited_items, audited = _resolve_historical_universe(
        settings=object(),
        use_live_snapshot=False,
        max_universe_symbols=0,
        start_date="2024-01-02",
        audited_pit_universe_path="/frozen/audited.sqlite3",
        expected_coverage_audit_sha256="a" * 64,
    )
    legacy_items, legacy = _resolve_historical_universe(
        settings=object(),
        use_live_snapshot=False,
        max_universe_symbols=0,
        start_date="2024-01-02",
        pit_universe_path="/legacy/not-trusted.sqlite3",
    )

    assert audited_items[0]["symbol"] == "audited"
    assert audited.final_oos_eligible is False
    assert legacy_items[0]["symbol"] == "legacy"
    assert legacy.source == "legacy"
    assert calls == [
        ("audited", "/frozen/audited.sqlite3", "a" * 64),
        ("legacy", "/legacy/not-trusted.sqlite3", None),
    ]

    with pytest.raises(ValueError, match="audited.*legacy|legacy.*audited|only one"):
        _resolve_historical_universe(
            settings=object(),
            use_live_snapshot=False,
            max_universe_symbols=0,
            start_date="2024-01-02",
            pit_universe_path="/legacy/universe.json",
            audited_pit_universe_path="/frozen/audited.sqlite3",
            expected_coverage_audit_sha256="a" * 64,
        )


def test_historical_resolver_seeds_only_through_requested_end_date(monkeypatch):
    seed_calls = []

    class Universe:
        end_date = "2024-12-31"

        def seed_items(self, start_date, end_date):
            seed_calls.append((start_date, end_date))
            return [{"symbol": "600001", "market": "a", "name": "历史名称"}]

    class Loader:
        @classmethod
        def from_file(cls, _path, *, expected_coverage_audit_sha256):
            assert expected_coverage_audit_sha256 == "a" * 64
            return Universe()

    monkeypatch.setattr(research_backtest, "AuditedPointInTimeUniverse", Loader)

    items, _universe = _resolve_historical_universe(
        settings=object(),
        use_live_snapshot=False,
        max_universe_symbols=0,
        start_date="2024-01-02",
        end_date="2024-03-29",
        audited_pit_universe_path="/frozen/audited.sqlite3",
        expected_coverage_audit_sha256="a" * 64,
    )

    assert items[0]["symbol"] == "600001"
    assert seed_calls == [("2024-01-02", "2024-03-29")]


def test_audited_resolver_closes_loader_when_seed_query_fails(monkeypatch):
    class Universe:
        end_date = "2024-01-03"

        def __init__(self):
            self.closed = False

        def seed_items(self, _start_date, _end_date):
            raise RuntimeError("seed failed")

        def close(self):
            self.closed = True

    universe = Universe()

    class Loader:
        @classmethod
        def from_file(cls, _path, *, expected_coverage_audit_sha256):
            assert expected_coverage_audit_sha256 == "a" * 64
            return universe

    monkeypatch.setattr(research_backtest, "AuditedPointInTimeUniverse", Loader)

    with pytest.raises(RuntimeError, match="seed failed"):
        _resolve_historical_universe(
            settings=object(),
            use_live_snapshot=False,
            max_universe_symbols=0,
            start_date="2024-01-02",
            audited_pit_universe_path="/frozen/audited.sqlite3",
            expected_coverage_audit_sha256="a" * 64,
        )
    assert universe.closed is True


def _historical_cli_authority_args():
    return [
        "--end-date", "2023-12-31",
        "--expected-artifact-root-sha256", "b" * 64,
        "--temporal-contract-path", "/frozen/contract.json",
        "--expected-temporal-contract-sha256", "c" * 64,
        "--expected-temporal-role", "development",
    ]


def _historical_composite_cli_authority_args():
    return [
        "--end-date", "2023-12-29",
        "--expected-composite-root-sha256", "d" * 64,
        "--temporal-contract-path", "/frozen/contract.json",
        "--expected-temporal-contract-sha256", "c" * 64,
        "--expected-temporal-role", "development",
    ]


def test_historical_cli_rejects_legacy_pit_universe_before_io(monkeypatch):
    monkeypatch.setattr(jobs, "get_settings", lambda: object())
    with pytest.raises(ValueError, match="legacy PIT universe is not allowed"):
        jobs.main(
            [
                "research-historical-universe",
                "--pit-universe-path",
                "/frozen/universe.json",
                *_historical_cli_authority_args(),
            ]
        )


def test_historical_cli_forwards_explicit_audited_sqlite_and_external_audit_root(
    monkeypatch, capsys
):
    captured = {}
    monkeypatch.setattr(jobs, "get_settings", lambda: object())

    def fake_backtest(**kwargs):
        captured.update(kwargs)
        return {"summary": {}, "qualified_trades": []}

    monkeypatch.setattr(jobs, "run_historical_universe_research_backtest", fake_backtest)

    assert (
        jobs.main(
            [
                "research-historical-universe",
                "--audited-pit-universe-path",
                "/frozen/audited.sqlite3",
                "--expected-coverage-audit-sha256",
                "a" * 64,
                *_historical_cli_authority_args(),
            ]
        )
        == 0
    )
    assert captured["audited_pit_universe_path"] == "/frozen/audited.sqlite3"
    assert captured["expected_coverage_audit_sha256"] == "a" * 64
    assert captured["pit_universe_path"] is None
    assert json.loads(capsys.readouterr().out)["summary"] == {}


def test_historical_cli_forwards_composite_descriptor_and_external_root(
    monkeypatch, capsys
):
    captured = {}
    monkeypatch.setattr(jobs, "get_settings", lambda: object())

    def fake_backtest(**kwargs):
        captured.update(kwargs)
        return {"summary": {}, "qualified_trades": []}

    monkeypatch.setattr(jobs, "run_historical_universe_research_backtest", fake_backtest)

    assert (
        jobs.main(
            [
                "research-historical-universe",
                "--composite-pit-descriptor-path",
                "/frozen/composite.json",
                *_historical_composite_cli_authority_args(),
            ]
        )
        == 0
    )
    assert captured["composite_pit_descriptor_path"] == "/frozen/composite.json"
    assert captured["expected_composite_root_sha256"] == "d" * 64
    assert captured["audited_pit_universe_path"] is None
    assert captured["pit_universe_path"] is None
    assert captured["expected_artifact_root_sha256"] is None
    assert captured["expected_coverage_audit_sha256"] is None
    assert json.loads(capsys.readouterr().out)["summary"] == {}


def test_historical_cli_rejects_single_and_composite_artifact_arguments(capsys):
    with pytest.raises(SystemExit) as exc:
        jobs.main(
            [
                "research-historical-universe",
                "--audited-pit-universe-path", "/frozen/single.sqlite3",
                "--composite-pit-descriptor-path", "/frozen/composite.json",
                *_historical_composite_cli_authority_args(),
            ]
        )

    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "extra_args", "expected_calls"),
    [
        ("research-historical-sweep", [], 1),
        ("research-historical-hold-sweep", ["--hold-days-list", "3,5"], 2),
    ],
)
def test_historical_sweep_clis_forward_audited_universe_contract(
    monkeypatch, capsys, command, extra_args, expected_calls
):
    captured = []
    sweep_captured = []
    monkeypatch.setattr(jobs, "get_settings", lambda: object())

    def fake_backtest(**kwargs):
        captured.append(kwargs)
        return {"summary": {}, "qualified_trades": []}

    monkeypatch.setattr(jobs, "run_historical_universe_research_backtest", fake_backtest)
    monkeypatch.setattr(
        jobs,
        "sweep_qualified_trades",
        lambda *args, **kwargs: (
            sweep_captured.append(kwargs) or {"top": [], "diagnostics": {}}
        ),
    )

    assert (
        jobs.main(
            [
                command,
                "--audited-pit-universe-path",
                "/frozen/audited.sqlite3",
                "--expected-coverage-audit-sha256",
                "a" * 64,
                *_historical_cli_authority_args(),
                *extra_args,
            ]
        )
        == 0
    )
    assert len(captured) == expected_calls
    assert all(
        call["audited_pit_universe_path"] == "/frozen/audited.sqlite3"
        and call["expected_coverage_audit_sha256"] == "a" * 64
        and call["pit_universe_path"] is None
        for call in captured
    )
    assert sweep_captured
    assert all(call["correlation_cache_dir"] is None for call in sweep_captured)
    assert json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("command", "correlation_args"),
    [
        ("research-historical-sweep", ["--correlation-threshold", "0.5"]),
        ("research-historical-hold-sweep", ["--correlation-threshold", "0.5"]),
        ("research-historical-sweep", ["--correlation-cache-dir", "/legacy/cache"]),
        ("research-historical-hold-sweep", ["--correlation-cache-dir", "/legacy/cache"]),
    ],
)
def test_frozen_historical_sweeps_reject_correlation_before_any_io(
    monkeypatch, command, correlation_args
):
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("settings/provider I/O")),
    )
    monkeypatch.setattr(
        jobs,
        "run_historical_universe_research_backtest",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("artifact I/O")),
    )
    with pytest.raises(ValueError, match="correlation cache"):
        jobs.main(
            [
                command,
                "--audited-pit-artifact-path", "/must-not-open/audited.sqlite3",
                "--expected-coverage-audit-sha256", "a" * 64,
                *correlation_args,
                *_historical_cli_authority_args(),
            ]
        )


@pytest.mark.parametrize(
    "command",
    [
        "research-historical-universe",
        "research-historical-sweep",
        "research-historical-hold-sweep",
    ],
)
def test_historical_clis_require_one_explicit_pit_artifact(command, capsys):
    with pytest.raises(SystemExit) as exc:
        jobs.main([command])

    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "--pit-universe-path" in stderr
    assert "--audited-pit-universe-path" in stderr


@pytest.mark.parametrize("command", ["research-backtest", "research-sweep"])
@pytest.mark.parametrize("start_date", ["2020-01-01", "2026-07-13"])
def test_legacy_mutable_research_clis_fail_before_any_io(
    monkeypatch, command, start_date
):
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("settings/provider I/O")),
    )
    monkeypatch.setattr(
        jobs,
        "run_candidate_research_backtest",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("strategy/cache I/O")),
    )
    with pytest.raises(
        ValueError,
        match="legacy mutable research command is disabled; use audited",
    ):
        jobs.main([command, "--start-date", start_date])


def test_build_pit_universe_cli_publishes_content_addressed_artifact(tmp_path, capsys):
    inputs = {
        "master.json": _master(),
        "calendar.json": _calendar(),
        "daily.json": _daily_universe(),
        "sources.json": _payload()["source_manifest"],
    }
    for filename, payload in inputs.items():
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    output_dir = tmp_path / "artifacts"

    assert (
        jobs.main(
            [
                "research-build-pit-universe",
                "--security-master-path",
                str(tmp_path / "master.json"),
                "--trade-calendar-path",
                str(tmp_path / "calendar.json"),
                "--daily-universe-path",
                str(tmp_path / "daily.json"),
                "--source-manifest-path",
                str(tmp_path / "sources.json"),
                "--start-date",
                "2024-01-02",
                "--end-date",
                "2024-01-04",
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["quality"]["final_oos_eligible"] is False
    assert PointInTimeUniverse.from_file(result["artifact"]["path"]).universe_sha256


def test_build_evidence_bundle_cli_is_explicitly_integrity_only(tmp_path, monkeypatch, capsys):
    authority = {
        "artifact_root_sha256": "1" * 64,
        "coverage_audit_sha256": "2" * 64,
        "temporal_contract_sha256": "3" * 64,
        "temporal_role": "development",
        "artifact_manifest_sha256": "4" * 64,
        "market_generation_root_sha256": "5" * 64,
        "stock_generation_lineage_sha256": "6" * 64,
    }
    original = _write_verified_evidence(tmp_path, authority)
    payload = json.loads(Path(original["path"]).read_text(encoding="utf-8"))
    pit_path = tmp_path / payload["artifacts"]["pit_universe"]["path"]
    market_path = tmp_path / payload["artifacts"]["market_data_manifest"]["path"]

    class FakeAudited:
        artifact_root_sha256 = authority["artifact_root_sha256"]
        coverage_audit_sha256 = authority["coverage_audit_sha256"]
        temporal_contract_sha256 = authority["temporal_contract_sha256"]
        temporal_role = "development"
        manifest = {
            "manifest_sha256": authority["artifact_manifest_sha256"],
            "coverage": {"start_date": "2024-01-02", "end_date": "2024-01-04"},
            "market_generations": {"root_sha256": authority["market_generation_root_sha256"]},
            "stock_generation": {"lineage_sha256": authority["stock_generation_lineage_sha256"]},
        }

        def close(self):
            return None

    monkeypatch.setattr(jobs.AuditedPointInTimeUniverse, "from_file", lambda *args, **kwargs: FakeAudited())

    assert jobs.main(
        [
            "research-build-evidence-bundle",
            "--pit-universe-path", str(pit_path),
            "--market-data-manifest-path", str(market_path),
            "--audited-pit-universe-path", str(tmp_path / "audited.sqlite3"),
            "--expected-coverage-audit-sha256", authority["coverage_audit_sha256"],
            "--expected-artifact-root-sha256", authority["artifact_root_sha256"],
            "--expected-temporal-contract-sha256", authority["temporal_contract_sha256"],
            "--expected-temporal-role", "development",
            "--output-dir", str(tmp_path),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "development_integrity_only"
    assert result["strict_validation_eligible"] is False
    assert result["evidence"]["eligibility"]["reasons"]


class _Settings:
    scan_min_amount = 0
    scan_min_price = 0
    scan_max_price = 1000


def _frame():
    dates = pd.date_range("2024-01-01", periods=6, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [10.0] * 6,
            "high": [10.5] * 6,
            "low": [9.5] * 6,
            "close": [10.0] * 6,
            "volume": [100_000_000] * 6,
            "amount": [1_000_000_000] * 6,
            "change_pct": [0.0] * 6,
            "ma20": [9.0] * 6,
            "ma60": [9.0] * 6,
            "return_20d": [0.01] * 6,
        }
    )


class _HistoricalMembership:
    universe_sha256 = "f" * 64
    calendar_sha256 = "e" * 64
    source_manifest_sha256 = "d" * 64

    def item_as_of(self, symbol, signal_date):
        if signal_date != "2024-01-03":
            return None
        return {"symbol": symbol, "market": "a", "name": "历史名称"}


def test_historical_candidate_map_uses_pit_name_and_membership():
    frames = {
        "600001": {
            "base": {"symbol": "600001", "market": "a", "name": "当前退市名称"},
            "frame": _frame(),
        }
    }

    candidates = _build_historical_candidate_maps(
        frames,
        start_date="2024-01-01",
        hold_days=1,
        max_deep=10,
        settings=_Settings(),
        universe_source=_HistoricalMembership(),
    )

    assert set(candidates) == {"2024-01-03"}
    assert candidates["2024-01-03"]["600001"]["name"] == "历史名称"


def test_historical_candidate_map_uses_one_batch_membership_query_per_date():
    frames = {
        symbol: {
            "base": {"symbol": symbol, "market": "a", "name": "当前名称"},
            "frame": _frame(),
        }
        for symbol in ("600001", "000002")
    }

    class BatchMembership:
        def __init__(self):
            self.calls = []

        def items_as_of(self, signal_date):
            self.calls.append(signal_date)
            if signal_date != "2024-01-03":
                return []
            return [
                {"symbol": "000002", "market": "a", "name": "ST历史名称"},
                {"symbol": "600001", "market": "a", "name": "历史名称"},
            ]

        def item_as_of(self, symbol, signal_date):
            raise AssertionError("batch membership path must not call item_as_of")

    membership = BatchMembership()

    candidates = _build_historical_candidate_maps(
        frames,
        start_date="2024-01-01",
        hold_days=1,
        max_deep=10,
        settings=_Settings(),
        universe_source=membership,
    )

    assert len(membership.calls) == len(set(membership.calls))
    assert candidates["2024-01-03"] == {
        "600001": candidates["2024-01-03"]["600001"]
    }
    assert candidates["2024-01-03"]["600001"]["name"] == "历史名称"


def test_historical_candidate_map_fails_closed_on_audited_batch_membership_error():
    class BrokenAuditedMembership:
        is_audited_store_artifact = True

        def close(self):
            pass
        start_date = "2024-01-01"

        def items_as_of(self, _signal_date):
            raise ValueError("audited membership read failed")

    with pytest.raises(ValueError, match="audited membership read failed"):
        _build_historical_candidate_maps(
            {
                "600001": {
                    "base": {"symbol": "600001", "market": "a", "name": "历史名称"},
                    "frame": _frame(),
                }
            },
            start_date="2024-01-01",
            hold_days=1,
            max_deep=10,
            settings=_Settings(),
            universe_source=BrokenAuditedMembership(),
        )


def test_batch_eligible_dates_fails_closed_on_audited_membership_error():
    class BrokenAuditedMembership:
        is_audited_store_artifact = True
        start_date = "2024-01-01"

        def items_as_of(self, _signal_date):
            raise ValueError("audited membership read failed")

    with pytest.raises(ValueError, match="audited membership read failed"):
        _batch_pit_eligible_dates_by_symbol(
            BrokenAuditedMembership(), {"2024-01-03"}
        )


def test_artifact_indicator_prefix_cancels_future_adjustment_scale():
    dates = pd.date_range("2023-01-03", periods=120, freq="B").strftime("%Y-%m-%d")
    raw_close = pd.Series(
        [10.0 + index * 0.03 + (index % 7) * 0.02 for index in range(120)]
    )
    raw_open = raw_close * 0.995
    raw_high = raw_close * 1.01
    raw_low = raw_close * 0.99
    bar_factor = pd.Series([1.0] * 60 + [2.0] * 60)
    analysis_end_factor = 10.0

    analysis_frame = add_indicators(
        pd.DataFrame(
            {
                "date": dates,
                "open": raw_open * bar_factor / analysis_end_factor,
                "high": raw_high * bar_factor / analysis_end_factor,
                "low": raw_low * bar_factor / analysis_end_factor,
                "close": raw_close * bar_factor / analysis_end_factor,
                "raw_open": raw_open,
                "raw_high": raw_high,
                "raw_low": raw_low,
                "raw_close": raw_close,
                "volume": [100_000 + index * 100 for index in range(120)],
                "amount": [1_000_000 + index * 1_000 for index in range(120)],
                "change_pct": [0.0] * 120,
            }
        )
    )
    signal_index = 99
    causal_factor = float(bar_factor.iloc[signal_index])
    expected_frame = add_indicators(
        pd.DataFrame(
            {
                "date": dates[: signal_index + 1],
                "open": raw_open.iloc[: signal_index + 1] * bar_factor.iloc[: signal_index + 1] / causal_factor,
                "high": raw_high.iloc[: signal_index + 1] * bar_factor.iloc[: signal_index + 1] / causal_factor,
                "low": raw_low.iloc[: signal_index + 1] * bar_factor.iloc[: signal_index + 1] / causal_factor,
                "close": raw_close.iloc[: signal_index + 1] * bar_factor.iloc[: signal_index + 1] / causal_factor,
                "volume": analysis_frame["volume"].iloc[: signal_index + 1],
            }
        )
    )

    actual_frame = research_backtest._artifact_causal_indicator_prefix(
        analysis_frame, signal_index
    )

    assert build_signal_snapshot(evaluate_signal(actual_frame)) == build_signal_snapshot(
        evaluate_signal(expected_frame)
    )
    assert actual_frame["raw_close"].tolist() == pytest.approx(
        raw_close.iloc[: signal_index + 1].tolist()
    )


def test_market_breadth_uses_same_pit_membership():
    frame = pd.concat([_frame()] * 12, ignore_index=True)
    frame["date"] = pd.date_range("2023-11-01", periods=len(frame), freq="D").strftime("%Y-%m-%d")
    membership_date = frame.iloc[60]["date"]

    class Membership:
        def item_as_of(self, symbol, signal_date):
            if signal_date == membership_date:
                return {"symbol": symbol, "name": "历史名称"}
            return None

    result = _historical_market_breadth(
        {"600001": {"base": {"symbol": "600001"}, "frame": frame}},
        start_date="2023-11-01",
        hold_days=1,
        universe_source=Membership(),
    )

    assert list(result) == [membership_date]
    assert result[membership_date]["sample_count"] == 1


def test_market_breadth_excludes_historical_st_member():
    frame = pd.concat([_frame()] * 11, ignore_index=True).iloc[:61].copy()
    frame["date"] = pd.date_range("2023-11-01", periods=len(frame), freq="D").strftime(
        "%Y-%m-%d"
    )
    signal_date = frame.iloc[60]["date"]

    class Membership:
        def item_as_of(self, symbol, date_value):
            if date_value != signal_date:
                return None
            return {"symbol": symbol, "name": "ST历史名称"}

    result = _historical_market_breadth(
        {"600001": {"base": {"symbol": "600001"}, "frame": frame}},
        start_date="2023-11-01",
        hold_days=1,
        universe_source=Membership(),
    )

    assert result == {}


def test_cross_section_membership_does_not_depend_on_future_frame_length():
    frame = _frame().iloc[:4].copy()

    candidates = _build_historical_candidate_maps(
        {"600001": {"base": {"symbol": "600001", "name": "历史名称"}, "frame": frame}},
        start_date="2024-01-01",
        hold_days=10,
        max_deep=10,
        settings=_Settings(),
    )

    assert "2024-01-03" in candidates

    breadth_frame = pd.concat([_frame()] * 11, ignore_index=True).iloc[:61].copy()
    breadth_frame["date"] = pd.date_range(
        "2023-11-01", periods=len(breadth_frame), freq="D"
    ).strftime("%Y-%m-%d")
    breadth = _historical_market_breadth(
        {"600001": {"base": {"symbol": "600001"}, "frame": breadth_frame}},
        start_date="2023-11-01",
        hold_days=10,
    )

    assert breadth_frame.iloc[60]["date"] in breadth


def test_pit_backtest_fails_closed_when_any_seed_symbol_history_is_missing(
    tmp_path, monkeypatch
):
    class Universe:
        end_date = "2024-01-04"

        def items_as_of(self, signal_date):
            return []

        def open_sessions(self, start_date, end_date):
            return []

    monkeypatch.setattr(
        research_backtest,
        "_resolve_historical_universe",
        lambda *args, **kwargs: (
            [{"symbol": "600001", "market": "a", "name": "历史退市股"}],
            Universe(),
        ),
    )

    class Provider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            if market == "a":
                raise RuntimeError("delisted history unavailable")
            return _frame(), "synthetic-etf"

    with pytest.raises(ValueError, match="legacy PIT universe is not allowed"):
        run_historical_universe_research_backtest(
            settings=_Settings(),
            provider=Provider(),
            start_date="2024-01-02",
            max_deep=10,
            top_n=1,
            hold_days=1,
            lookback_days=20,
            max_universe_symbols=0,
            cache_dir=str(tmp_path / "cache"),
            pit_universe_path="unused-by-monkeypatch",
        )


def test_audited_backtest_closes_universe_when_history_fetch_fails(tmp_path, monkeypatch):
    class Universe:
        start_date = "2023-08-25"
        end_date = "2024-01-04"
        is_audited_store_artifact = True

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def items_as_of(self, signal_date):
            return []

        def open_sessions(self, start_date, end_date):
            return pd.bdate_range(start_date, end_date).strftime("%Y-%m-%d").tolist()

    universe = Universe()
    class FailingArtifactAdapter:
        def __init__(self, value, **kwargs):
            assert value is universe

        def signal_frame(self, symbol, start_date, as_of_date):
            raise RuntimeError("artifact history failed")

    monkeypatch.setattr(
        research_backtest, "ArtifactNativeReplayAdapter", FailingArtifactAdapter
    )
    monkeypatch.setattr(
        research_backtest,
        "_resolve_historical_universe",
        lambda *args, **kwargs: (
            [{"symbol": "600001", "market": "a", "name": "历史退市股"}],
            universe,
        ),
    )
    monkeypatch.setattr(
        research_backtest,
        "load_temporal_partition_contract",
        lambda path: {"contract_sha256": "c" * 64},
    )
    monkeypatch.setattr(research_backtest, "assert_range_allowed", lambda *args: None)

    class Provider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            raise AssertionError("audited artifact path must not call provider")

    with pytest.raises(ValueError, match="PIT universe history is incomplete"):
        run_historical_universe_research_backtest(
            settings=_Settings(),
            provider=Provider(),
            start_date="2024-01-02",
            end_date="2024-01-04",
            max_deep=10,
            top_n=1,
            hold_days=1,
            lookback_days=20,
            max_universe_symbols=0,
            cache_dir=str(tmp_path / "cache"),
            audited_pit_universe_path="unused-by-monkeypatch",
            expected_coverage_audit_sha256="a" * 64,
            expected_artifact_root_sha256="b" * 64,
            temporal_contract_path="synthetic-contract",
            expected_temporal_contract_sha256="c" * 64,
            expected_temporal_role="development",
        )
    assert universe.closed is True


def test_indicator_failure_is_not_mislabeled_as_pit_history_gap(tmp_path, monkeypatch):
    class Universe:
        start_date = "2023-08-25"
        end_date = "2024-01-04"
        is_audited_store_artifact = True

        def close(self):
            pass

        def items_as_of(self, signal_date):
            return []

        def open_sessions(self, start_date, end_date):
            return pd.bdate_range(start_date, end_date).strftime("%Y-%m-%d").tolist()

    universe = Universe()

    class ArtifactAdapter:
        def __init__(self, value, **kwargs):
            assert value is universe

        def signal_frame(self, symbol, start_date, as_of_date):
            return _frame()

    monkeypatch.setattr(research_backtest, "ArtifactNativeReplayAdapter", ArtifactAdapter)
    monkeypatch.setattr(
        research_backtest,
        "_resolve_historical_universe",
        lambda *args, **kwargs: (
            [{"symbol": "600001", "market": "a", "name": "历史股票"}],
            universe,
        ),
    )
    monkeypatch.setattr(
        research_backtest,
        "load_temporal_partition_contract",
        lambda path: {"contract_sha256": "c" * 64},
    )
    monkeypatch.setattr(research_backtest, "assert_range_allowed", lambda *args: None)
    monkeypatch.setattr(
        research_backtest,
        "add_indicators",
        lambda frame: (_ for _ in ()).throw(RuntimeError("indicator fixture failed")),
    )

    with pytest.raises(RuntimeError, match="indicator fixture failed"):
        run_historical_universe_research_backtest(
            settings=_Settings(),
            provider=object(),
            start_date="2024-01-02",
            end_date="2024-01-04",
            max_deep=10,
            top_n=1,
            hold_days=1,
            lookback_days=20,
            max_universe_symbols=0,
            cache_dir=str(tmp_path / "cache"),
            audited_pit_universe_path="unused-by-monkeypatch",
            expected_coverage_audit_sha256="a" * 64,
            expected_artifact_root_sha256="b" * 64,
            temporal_contract_path="synthetic-contract",
            expected_temporal_contract_sha256="c" * 64,
            expected_temporal_role="development",
        )


def test_prior_quality_membership_rejects_uncovered_nonmember_and_historical_st():
    class Universe:
        start_date = "2024-01-02"

        def item_as_of(self, symbol, signal_date):
            if signal_date == "2024-01-02":
                return {"symbol": symbol, "name": "ST历史名称"}
            if signal_date == "2024-01-03":
                return {"symbol": symbol, "name": "历史正常名称"}
            return None

    universe = Universe()
    assert _pit_historical_member(universe, "600001", "2024-01-01") is None
    assert _pit_historical_member(universe, "600001", "2024-01-02") is None
    assert _pit_historical_member(universe, "600001", "2024-01-03")["name"] == "历史正常名称"
    assert _pit_historical_member(universe, "600001", "2024-01-04") is None
