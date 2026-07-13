import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import jobs
from app.research_pit import (
    build_pit_universe_payload,
    write_frozen_market_data_manifest,
    write_pit_universe_artifact,
    write_research_evidence_bundle,
)
from app.research_validation import (
    append_experiment_event,
    build_purged_walk_forward_partitions,
    qualified_trades_sha256,
    verify_artifact_trade_lineage,
    read_experiment_ledger,
    run_frozen_strategy_validation,
    validate_point_in_time_contract,
    wilson_interval,
    _unique_trades,
)


_TEMPORAL_CONTRACT_PATH = Path("data/research_partitions/frozen-v1.json")
_TEMPORAL_CONTRACT_SHA256 = json.loads(
    _TEMPORAL_CONTRACT_PATH.read_text(encoding="utf-8")
)["contract_sha256"]


def _temporal_cli_args(start_date="2020-01-01", end_date="2023-12-31"):
    return [
        "--audited-pit-universe-path", "synthetic-audited-artifact",
        "--start-date", start_date,
        "--end-date", end_date,
        "--temporal-contract-path", str(_TEMPORAL_CONTRACT_PATH),
        "--expected-coverage-audit-sha256", "d" * 64,
        "--expected-artifact-root-sha256", "e" * 64,
        "--expected-temporal-contract-sha256", _TEMPORAL_CONTRACT_SHA256,
        "--expected-temporal-role", "development",
    ]


@pytest.fixture(autouse=True)
def _synthetic_audited_loader(monkeypatch):
    class SyntheticAuditedUniverse:
        artifact_root_sha256 = "e" * 64
        coverage_audit_sha256 = "d" * 64
        temporal_contract_sha256 = _TEMPORAL_CONTRACT_SHA256
        temporal_role = "development"
        manifest = {"coverage": {"start_date": "2016-01-01", "end_date": "2023-12-31"}}

        def item_as_of(self, symbol, signal_date):
            return {"symbol": symbol, "signal_date": signal_date}

        def close(self):
            return None

    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: SyntheticAuditedUniverse(),
    )


def _mock_validated_contract(summary):
    return {
        **summary["research_data_contract"],
        "verified_authority": {
            "artifact_root_sha256": "e" * 64,
            "coverage_audit_sha256": "d" * 64,
        },
    }


def _trade(signal_date: date, return_pct: float = 2.0, hold_days: int = 2, symbol: str = "600001"):
    return {
        "symbol": symbol,
        "signal_date": signal_date.isoformat(),
        "entry_date": (signal_date + timedelta(days=1)).isoformat(),
        "exit_date": (signal_date + timedelta(days=hold_days)).isoformat(),
        "return_pct": return_pct,
        "max_adverse_pct": min(return_pct, -1.0),
        "rank_score": 5,
        "market_level": "favorable",
        "signal_tags": ["frozen_signal"],
    }


class _LineageUniverse:
    start_date = "2020-01-01"
    end_date = "2020-01-10"

    def __init__(self):
        self.calls = []

    def item_as_of(self, symbol, signal_date):
        return {"symbol": symbol, "signal_date": signal_date}

    def open_sessions(self, start_date, end_date):
        return ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        self.calls.append(("signal", symbol, start_date, as_of_date))
        return [{"trade_date": as_of_date, "generation_proof": {"trade_date": as_of_date}}]

    def next_open_execution_evidence(self, symbol, trade_date, side):
        self.calls.append(("open", symbol, trade_date, side))
        proof = {"trade_date": trade_date, "side": side, "generation_id": f"g-{trade_date}"}
        raw_price = 10.0 if side == "buy" else 11.0
        return {"fillable": True, "reason": "raw_open", "raw_price": raw_price, "generation_proof": proof}


def _lineage_trade():
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


def test_artifact_trade_lineage_rechecks_fresh_entry_and_exit_proofs():
    universe = _LineageUniverse()
    digest = verify_artifact_trade_lineage(universe, [_lineage_trade()])

    assert len(digest) == 64
    assert ("open", "600001", "2020-01-02", "buy") in universe.calls
    assert ("open", "600001", "2020-01-04", "sell") in universe.calls


def test_artifact_trade_lineage_rejects_tampered_execution_claim():
    universe = _LineageUniverse()
    trade = _lineage_trade()
    trade["entry_executability"]["raw_price"] = 10.5

    with pytest.raises(ValueError, match="trade lineage"):
        verify_artifact_trade_lineage(universe, [trade])


def _point_in_time_contract(trades):
    return {
        "schema_version": "research_data_contract/v1",
        "artifact_role": "development_only",
        "point_in_time": True,
        "eligible_for_final_validation": True,
        "entry_decision_cutoff": "next_open",
        "known_biases": [],
        "qualified_trades_sha256": qualified_trades_sha256(trades),
        "universe_sha256": "a" * 64,
        "calendar_sha256": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "coverage_audit_sha256": "d" * 64,
        "artifact_root_sha256": "e" * 64,
    }


def _verified_point_in_time_contract(trades, root):
    first = min(date.fromisoformat(item["signal_date"]) for item in trades)
    last = max(date.fromisoformat(item["exit_date"]) for item in trades)
    sessions = []
    current = first
    while current <= last:
        sessions.append(current.isoformat())
        current += timedelta(days=1)
    symbols = sorted({item["symbol"] for item in trades})
    master = []
    for symbol in symbols:
        suffix = "SH" if symbol.startswith("6") else "SZ"
        master.append(
            {
                "ts_code": f"{symbol}.{suffix}",
                "symbol": symbol,
                "name": f"股票{symbol}",
                "exchange": "SSE" if suffix == "SH" else "SZSE",
                "list_status": "L",
                "list_date": "20000101",
                "delist_date": None,
            }
        )
    daily = [
        {
            "trade_date": session,
            "ts_code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
            "name": f"历史{symbol}",
            "industry": "测试",
        }
        for session in sessions
        for symbol in symbols
    ]
    raw_dir = root / "universe-source"
    raw_dir.mkdir(exist_ok=True)
    source_manifest = {"provider": "synthetic-validator-source", "raw_artifacts": {}}
    for name in ("stock_basic", "bak_basic", "trade_cal"):
        path = raw_dir / f"{name}.json"
        path.write_text(json.dumps([{"source": name}]), encoding="utf-8")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        source_manifest[f"{name}_raw_sha256"] = digest
        source_manifest["raw_artifacts"][name] = {
            "path": str(path.relative_to(root)),
            "sha256": digest,
            "bytes": len(content),
        }
    universe_payload = build_pit_universe_payload(
        security_master=master,
        trade_calendar=sessions,
        daily_universe=daily,
        source_manifest=source_manifest,
        start_date=first.isoformat(),
        end_date=last.isoformat(),
    )
    universe = write_pit_universe_artifact(str(root), universe_payload)

    market_dir = root / "market"
    market_dir.mkdir(exist_ok=True)
    component_rows = {
        "raw_execution_bars": [
            {"date": first.isoformat(), "symbol": symbols[0], "open": 10.0}
        ],
        "corporate_actions": [
            {"ex_date": first.isoformat(), "symbol": symbols[0], "ratio": 0.0}
        ],
        "causal_signal_bars": [
            {"date": first.isoformat(), "symbol": symbols[0], "close": 10.0}
        ],
    }
    component_paths = {}
    for name, rows in component_rows.items():
        path = market_dir / f"{name}.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        component_paths[name] = str(path)
    market = write_frozen_market_data_manifest(
        str(root),
        components=component_paths,
        start_date=first.isoformat(),
        end_date=last.isoformat(),
        source={
            "provider": "synthetic-validator-market",
            "knowledge_cutoff": f"{last.isoformat()}T15:05:00+08:00",
        },
    )
    evidence = write_research_evidence_bundle(
        str(root),
        pit_universe_path=universe["path"],
        market_data_manifest_path=market["path"],
    )
    return {
        **_point_in_time_contract(trades),
        "universe_sha256": evidence["universe_sha256"],
        "calendar_sha256": evidence["calendar_sha256"],
        "source_manifest_sha256": evidence["source_manifest_sha256"],
        "evidence_bundle_path": evidence["filename"],
        "evidence_bundle_sha256": evidence["evidence_bundle_sha256"],
    }


def test_point_in_time_contract_rejects_self_asserted_hash_strings(tmp_path):
    trades = [_trade(date(2024, 1, 1))]

    with pytest.raises(ValueError, match="verified evidence bundle"):
        validate_point_in_time_contract(
            {"research_data_contract": _point_in_time_contract(trades)},
            trades,
            artifact_base_dir=str(tmp_path),
        )


def test_point_in_time_contract_rejects_symlinked_evidence_bundle(tmp_path):
    trades = [_trade(date(2024, 1, 1))]
    contract = _verified_point_in_time_contract(trades, tmp_path)
    link = tmp_path / "evidence-link.json"
    link.symlink_to(tmp_path / contract["evidence_bundle_path"])
    contract["evidence_bundle_path"] = link.name

    with pytest.raises(ValueError, match="verified evidence bundle"):
        validate_point_in_time_contract(
            {"research_data_contract": contract},
            trades,
            artifact_base_dir=str(tmp_path),
        )


def test_integrity_only_evidence_bundle_cannot_claim_final_validation(tmp_path):
    trades = [_trade(date(2024, 1, 1))]
    contract = _verified_point_in_time_contract(trades, tmp_path)

    with pytest.raises(ValueError, match="point-in-time contract"):
        validate_point_in_time_contract(
            {"research_data_contract": contract},
            trades,
            artifact_base_dir=str(tmp_path),
        )


def test_walk_forward_partitions_purge_overlap_without_loading_final_oos():
    start = date(2024, 1, 1)
    trades = [_trade(start + timedelta(days=offset), hold_days=4) for offset in range(40)]

    result = build_purged_walk_forward_partitions(
        trades,
        train_days=14,
        validation_days=7,
        step_days=7,
        embargo_days=2,
        final_oos_start="2024-02-10",
    )

    assert result["folds"]
    assert result["final_oos"] == {"status": "not_loaded", "start_date": "2024-02-10"}
    assert result["boundary_purged_trade_count"] == 4

    for fold in result["folds"]:
        validation_start = fold["metadata"]["validation_start"]
        assert all(item["exit_date"] < validation_start for item in fold["train_trades"])
        assert all(
            validation_start <= item["signal_date"] < fold["metadata"]["validation_end"]
            for item in fold["validation_trades"]
        )
        assert all(
            item["exit_date"] < fold["metadata"]["validation_end"]
            for item in fold["validation_trades"]
        )
        assert all(item["signal_date"] < "2024-02-10" for item in fold["validation_trades"])
        assert all(item["exit_date"] < "2024-02-10" for item in fold["validation_trades"])


def test_walk_forward_rejects_development_file_containing_final_oos_rows():
    with pytest.raises(ValueError, match="contains final OOS rows"):
        build_purged_walk_forward_partitions(
            [_trade(date(2024, 2, 10))],
            train_days=14,
            validation_days=7,
            step_days=7,
            embargo_days=2,
            final_oos_start="2024-02-10",
        )


def test_overlapping_folds_deduplicate_the_same_economic_trade():
    trade = _trade(date(2024, 1, 2))
    variant = {**trade, "diagnostic_note": "same trade with different metadata"}

    unique = _unique_trades(
        [{"validation_trades": [trade]}, {"validation_trades": [variant]}],
        "validation_trades",
    )

    assert unique == [trade]


def test_wilson_interval_rejects_twenty_trade_success_story():
    lower, upper = wilson_interval(17, 20)

    assert lower == pytest.approx(0.6396, abs=0.001)
    assert upper == pytest.approx(0.9476, abs=0.001)
    assert lower < 0.70


def test_frozen_validation_never_loads_final_oos():
    start = date(2020, 1, 1)
    development = [
        _trade(
            start + timedelta(days=offset),
            3.0 if offset % 5 else -1.0,
            symbol=f"{600000 + offset:06d}",
        )
        for offset in range(70)
    ]
    report = run_frozen_strategy_validation(
        development,
        strategy={
            "hold_days": 2,
            "top_n": 10,
            "symbol_cooldown_days": 0,
            "max_active_positions": 0,
            "required_signal_tags": ["frozen_signal"],
            "market_levels": ["favorable"],
            "exposure_multiplier": 1.0,
            "annual_financing_rate_pct": 8.0,
            "roundtrip_cost_bps": 25.0,
            "slippage_bps": 10.0,
            "capital_model": "slot-daily",
        },
        validation={
            "train_days": 21,
            "validation_days": 7,
            "step_days": 7,
            "embargo_days": 2,
            "final_oos_start": "2026-07-13",
            "minimum_oos_trades": 200,
        },
    )

    assert report["final_oos"] == {"status": "not_loaded", "start_date": "2026-07-13"}
    assert report["boundary_purged_trade_count"] == 0
    assert report["aggregate_validation"]["selected_trade_count"] > 0
    assert report["aggregate_validation"]["trade_win_rate_pct"] > 70
    selection = report["strategy_selection_replay"]
    assert selection["bound"] is False
    assert selection["selection_receipt_bound"] is False
    assert selection["reasons"] == [
        "independent_strategy_selection_replay_not_bound"
    ]
    assert selection["candidate_pool_sha256"] == report["dataset_sha256"]
    assert selection["strategy_sha256"] == report["strategy_sha256"]
    assert selection["components"]["portfolio_selector"]["source_sha256"]
    assert selection["components"]["fixed_sweep_engine"]["source_sha256"]
    assert selection["limitations"] == [
        "candidate_generation_completeness_bound_by_producer_identity_not_independent_rebuild"
    ]
    assert report["qualification"]["minimum_sample_pass"] is False
    assert report["qualification"]["signal_days_pass"] is False
    assert report["qualification"]["profile_primary_gates_pass"] is False
    assert report["qualification"]["full_rolling_12m_pass"] is False
    assert report["qualification"]["development_primary_gates_pass"] is False
    assert report["qualification"]["completion_pass"] is False
    assert report["qualification"]["all_pass"] is False


def test_frozen_validation_with_empty_filters_does_not_select_a_better_regime():
    start = date(2020, 1, 1)
    trades = []
    for offset in range(80):
        trade = _trade(
            start + timedelta(days=offset),
            4.0 if offset % 2 == 0 else -4.0,
            symbol=f"{602000 + offset:06d}",
        )
        trade["market_level"] = "favorable" if offset % 2 == 0 else "defensive"
        trades.append(trade)

    report = run_frozen_strategy_validation(
        trades,
        strategy={
            "hold_days": 2,
            "top_n": 10,
            "required_signal_tags": [],
            "excluded_signal_tags": [],
            "market_levels": [],
            "capital_model": "signal-day",
        },
        validation={
            "train_days": 21,
            "validation_days": 7,
            "step_days": 7,
            "embargo_days": 2,
            "final_oos_start": "2026-07-13",
            "minimum_oos_trades": 1,
        },
    )

    assert report["aggregate_validation"]["trade_win_rate_pct"] < 70


def test_validation_rejects_exposure_above_2_08():
    with pytest.raises(ValueError, match="exposure_multiplier must be <= 2.08"):
        run_frozen_strategy_validation(
            [_trade(date(2020, 1, 1))],
            strategy={"exposure_multiplier": 6.0},
            validation={
                "train_days": 21,
                "validation_days": 7,
                "step_days": 7,
                "embargo_days": 2,
                "final_oos_start": "2026-07-13",
                "minimum_oos_trades": 1,
            },
        )


def test_frozen_validation_rejects_correlation_before_cache_reader(monkeypatch):
    monkeypatch.setattr(
        "app.research_validation.sweep_qualified_trades",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache reader")),
    )
    with pytest.raises(ValueError, match="correlation cache"):
        run_frozen_strategy_validation(
            [_trade(date(2020, 1, 1))],
            strategy={"correlation_threshold": 0.5, "correlation_cache_dir": "/secret/cache"},
            validation={
                "train_days": 21,
                "validation_days": 7,
                "step_days": 7,
                "embargo_days": 2,
                "final_oos_start": "2026-07-13",
            },
        )


def test_verified_contract_rejects_trade_outside_declared_range(tmp_path):
    trades = [_trade(date(2020, 1, 1)), _trade(date(2020, 1, 10))]
    contract = _verified_point_in_time_contract(trades, tmp_path)
    with pytest.raises(ValueError, match="outside declared validation range"):
        validate_point_in_time_contract(
            {"research_data_contract": contract},
            trades,
            artifact_base_dir=str(tmp_path),
            declared_start_date="2020-01-05",
            declared_end_date="2020-01-12",
        )


def test_development_gates_never_claim_completion_without_final_and_stress():
    start = date(2022, 1, 1)
    trades = [
        _trade(start + timedelta(days=offset), 2.0, symbol=f"{603000 + offset:06d}")
        for offset in range(600)
    ]

    report = run_frozen_strategy_validation(
        trades,
        strategy={
            "hold_days": 2,
            "top_n": 10,
            "symbol_cooldown_days": 0,
            "max_active_positions": 0,
            "required_signal_tags": ["frozen_signal"],
            "market_levels": ["favorable"],
            "exposure_multiplier": 1.0,
            "capital_model": "signal-day",
            "target_one_year_return_pct": 1.0,
        },
        validation={
            "train_days": 100,
            "validation_days": 100,
            "step_days": 100,
            "embargo_days": 2,
            "final_oos_start": "2026-07-13",
            "minimum_oos_trades": 50,
        },
    )

    assert report["qualification"]["development_primary_gates_pass"] is True
    assert report["qualification"]["completion_pass"] is False
    assert report["qualification"]["all_pass"] is False
    assert set(report["qualification"]["missing_completion_gates"]) >= {
        "sealed_final_oos",
        "baseline_and_double_cost_matrix",
        "regime_validation",
        "multiple_testing_adjustment",
    }


def test_development_gates_use_realistic_return_drawdown_and_quality_targets():
    start = date(2022, 1, 1)
    trades = [
        _trade(
            start + timedelta(days=offset),
            3.0 if offset % 5 != 0 else -1.0,
            symbol=f"{604000 + offset:06d}",
        )
        for offset in range(600)
    ]

    report = run_frozen_strategy_validation(
        trades,
        strategy={
            "hold_days": 2,
            "top_n": 10,
            "required_signal_tags": ["frozen_signal"],
            "market_levels": ["favorable"],
            "exposure_multiplier": 1.0,
            "capital_model": "signal-day",
            "target_drawdown_pct": 15.0,
            "target_win_rate_pct": 52.0,
            "target_profit_factor": 1.3,
            "target_calmar": 1.5,
        },
        validation={
            "train_days": 100,
            "validation_days": 100,
            "step_days": 100,
            "embargo_days": 2,
            "final_oos_start": "2026-07-13",
            "minimum_oos_trades": 50,
        },
    )

    qualification = report["qualification"]
    assert qualification["profit_factor_pass"] is True
    assert qualification["calmar_pass"] is True
    assert qualification["observed_win_rate_pct"] >= 52.0
    assert qualification["target_profile"] == "primary_50_return_15_drawdown"


def test_experiment_event_ledger_proves_registration_precedes_result(tmp_path):
    ledger = tmp_path / "experiment-events.jsonl"
    registered = append_experiment_event(
        str(ledger),
        {
            "event_id": "wf-003:registered",
            "experiment_id": "wf-003",
            "event_type": "registered",
            "hypothesis": "No-filter frozen baseline survives OOS",
            "falsification_criterion": "Wilson lower bound below 70%",
            "exit_criterion": "Reject after the frozen validation run",
        },
    )
    completed = append_experiment_event(
        str(ledger),
        {
            "event_id": "wf-003:completed",
            "experiment_id": "wf-003",
            "event_type": "completed",
            "result": {"all_pass": False},
        },
    )

    assert completed["previous_record_hash"] == registered["record_hash"]
    with pytest.raises(ValueError, match="event_id already exists"):
        append_experiment_event(
            str(ledger),
            {
                "event_id": "wf-003:completed",
                "experiment_id": "wf-003",
                "event_type": "completed",
            },
        )


def test_experiment_ledger_rejects_tampered_history(tmp_path):
    ledger = tmp_path / "tampered.jsonl"
    append_experiment_event(
        str(ledger),
        {
            "event_id": "wf-005:registered",
            "experiment_id": "wf-005",
            "event_type": "registered",
            "hypothesis": "Original hypothesis",
        },
    )
    row = json.loads(ledger.read_text(encoding="utf-8"))
    row["hypothesis"] = "Tampered hypothesis"
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="record hash mismatch"):
        append_experiment_event(
            str(ledger),
            {
                "event_id": "wf-005:completed",
                "experiment_id": "wf-005",
                "event_type": "completed",
            },
        )


def test_experiment_ledger_enforces_lifecycle_transitions(tmp_path):
    ledger = tmp_path / "states.jsonl"
    append_experiment_event(
        str(ledger),
        {
            "event_id": "wf-006:registered",
            "experiment_id": "wf-006",
            "event_type": "registered",
        },
    )

    with pytest.raises(ValueError, match="invalid experiment transition"):
        append_experiment_event(
            str(ledger),
            {
                "event_id": "wf-006:decision",
                "experiment_id": "wf-006",
                "event_type": "decision",
            },
        )
    append_experiment_event(
        str(ledger),
        {
            "event_id": "wf-006:completed",
            "experiment_id": "wf-006",
            "event_type": "completed",
        },
    )
    with pytest.raises(ValueError, match="invalid experiment transition"):
        append_experiment_event(
            str(ledger),
            {
                "event_id": "wf-006:failed",
                "experiment_id": "wf-006",
                "event_type": "failed",
            },
        )


def test_experiment_ledger_serializes_concurrent_appends(tmp_path):
    ledger = tmp_path / "concurrent.jsonl"

    def register(index):
        return append_experiment_event(
            str(ledger),
            {
                "event_id": f"wf-concurrent-{index}:registered",
                "experiment_id": f"wf-concurrent-{index}",
                "event_type": "registered",
            },
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(register, range(20)))

    rows = read_experiment_ledger(str(ledger))
    assert len(rows) == 20
    assert [row["sequence"] for row in rows] == list(range(1, 21))
    assert len({row["event_id"] for row in rows}) == 20
    with pytest.raises(ValueError, match="must be registered first"):
        append_experiment_event(
            str(ledger),
            {
                "event_id": "wf-004:completed",
                "experiment_id": "wf-004",
                "event_type": "completed",
            },
        )


def test_research_validate_file_cli_registers_then_records_result(tmp_path, capsys, monkeypatch):
    start = date(2020, 1, 1)
    trades = [
        _trade(start + timedelta(days=offset), 3.0 if offset % 5 else -1.0) for offset in range(70)
    ]
    source = tmp_path / "qualified.json"
    source.write_text(
        json.dumps(
            {
                "summary": {"research_data_contract": _point_in_time_contract(trades)},
                "qualified_trades": trades,
            }
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.jsonl"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda summary, rows, **kwargs: _mock_validated_contract(summary),
    )

    exit_code = jobs.main(
        [
            "research-validate-file",
            "--qualified-trades-path",
            str(source),
            "--experiment-id",
            "wf-cli-001",
            "--hypothesis",
            "The frozen signal survives purged validation",
            "--expected-mechanism",
            "Trend continuation",
            "--falsification-criterion",
            "Wilson lower bound is below 70%",
            "--exit-criterion",
            "Reject when any primary gate fails",
            "--final-oos-start",
            "2026-07-13",
            *_temporal_cli_args(),
            "--train-days",
            "21",
            "--validation-days",
            "7",
            "--step-days",
            "7",
            "--embargo-days",
            "2",
            "--minimum-oos-trades",
            "200",
            "--required-signal-tags",
            "frozen_signal",
            "--market-levels",
            "favorable",
            "--ledger-path",
            str(ledger),
            "--artifact-dir",
            str(artifact_dir),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert output["experiment_id"] == "wf-cli-001"
    assert output["final_oos"]["status"] == "not_loaded"
    assert output["qualification"]["all_pass"] is False
    assert [item["event_type"] for item in events] == ["registered", "completed"]
    artifact = events[-1]["report_artifact"]
    assert artifact["sha256"] == output["report_artifact"]["sha256"]
    assert artifact["bytes"] > 0
    saved_report = json.loads((artifact_dir / artifact["filename"]).read_text(encoding="utf-8"))
    assert saved_report["experiment_id"] == "wf-cli-001"
    assert saved_report["folds"] == output["folds"]


def test_research_validate_file_rejects_legacy_lookahead_contract(tmp_path):
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps(
            {
                "summary": {
                    "artifact_root_sha256": "e" * 64,
                    "coverage_audit_sha256": "d" * 64,
                },
                "qualified_trades": [_trade(date(2020, 1, 1))],
            }
        ),
        encoding="utf-8",
    )

    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="point-in-time contract"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path",
                str(source),
                "--experiment-id",
                "legacy-001",
                "--hypothesis",
                "Legacy data is safe",
                "--expected-mechanism",
                "Unknown",
                "--falsification-criterion",
                "Contract absent",
                "--exit-criterion",
                "Reject",
                "--final-oos-start",
                "2026-07-13",
                *_temporal_cli_args(),
                "--ledger-path",
                str(ledger),
            ]
        )
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [item["event_type"] for item in events] == ["registered", "failed"]


def test_partitions_reject_rows_outside_frozen_development_range():
    with pytest.raises(ValueError, match="development range"):
        build_purged_walk_forward_partitions(
            [_trade(date(2024, 1, 1))],
            train_days=30,
            validation_days=10,
            step_days=10,
            embargo_days=2,
            final_oos_start="2026-07-13",
        )


def test_diagnostic_trades_are_marked_and_excluded_from_promotion_folds():
    promoted = _trade(date(2020, 1, 1), symbol="600001")
    diagnostic = {
        **_trade(date(2020, 2, 1), symbol="600002"),
        "temporal_role": "contaminated_diagnostic",
    }
    result = build_purged_walk_forward_partitions(
        [promoted, diagnostic],
        train_days=10,
        validation_days=5,
        step_days=5,
        embargo_days=1,
        final_oos_start="2026-07-13",
    )
    assert result["contaminated_diagnostic"] == {
        "trade_count": 1,
        "promotion": False,
    }
    assert result["development_trade_count"] == 1


def test_partitions_require_frozen_final_oos_boundary():
    with pytest.raises(ValueError, match="2026-07-13"):
        run_frozen_strategy_validation(
            [_trade(date(2020, 1, 1))],
            strategy={"hold_days": 5},
            validation={
                "train_days": 30,
                "validation_days": 10,
                "step_days": 10,
                "embargo_days": 2,
                "final_oos_start": "2024-01-01",
            },
        )


def test_sweep_file_rejects_diagnostic_role_before_payload_io(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "_load_qualified_trades_payload",
        lambda *args: (_ for _ in ()).throw(AssertionError("payload I/O")),
    )
    args = _temporal_cli_args()
    args[args.index("development")] = "contaminated_diagnostic"
    with pytest.raises(ValueError, match="ordinary sweep.*development"):
        jobs.main(
            [
                "research-sweep-file",
                "--qualified-trades-path", "must-not-open.json",
                *args,
            ]
        )


@pytest.mark.parametrize(
    "correlation_args",
    [
        ["--correlation-threshold", "0.5"],
        ["--correlation-cache-dir", "/legacy/cache"],
    ],
)
def test_sweep_file_rejects_correlation_before_artifact_or_cache_io(
    monkeypatch, correlation_args
):
    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("artifact/cache I/O")),
    )
    with pytest.raises(ValueError, match="correlation cache"):
        jobs.main(
            [
                "research-sweep-file",
                "--qualified-trades-path", "must-not-open.json",
                *correlation_args,
                *_temporal_cli_args(),
            ]
        )


def test_sweep_file_default_correlation_cache_is_none_and_reaches_artifact(monkeypatch):
    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("artifact stage")),
    )
    with pytest.raises(AssertionError, match="artifact stage"):
        jobs.main(
            [
                "research-sweep-file",
                "--qualified-trades-path", "must-not-open.json",
                *_temporal_cli_args(),
            ]
        )


def test_validate_file_rejects_diagnostic_role_before_ledger_or_payload_io(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "_load_qualified_trades_payload",
        lambda *args: (_ for _ in ()).throw(AssertionError("payload I/O")),
    )
    monkeypatch.setattr(
        jobs,
        "append_experiment_event",
        lambda *args: (_ for _ in ()).throw(AssertionError("ledger I/O")),
    )
    args = _temporal_cli_args()
    args[args.index("development")] = "contaminated_diagnostic"
    with pytest.raises(ValueError, match="ordinary validation.*development"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path", "must-not-open.json",
                "--experiment-id", "pre-io",
                "--hypothesis", "x",
                "--expected-mechanism", "x",
                "--falsification-criterion", "x",
                "--exit-criterion", "x",
                "--final-oos-start", "2026-07-13",
                *args,
            ]
        )


def test_validate_file_rejects_explicit_correlation_cache_before_ledger_io(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "append_experiment_event",
        lambda *args: (_ for _ in ()).throw(AssertionError("ledger I/O")),
    )
    with pytest.raises(ValueError, match="correlation cache"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path", "must-not-open.json",
                "--experiment-id", "pre-io-cache",
                "--hypothesis", "x",
                "--expected-mechanism", "x",
                "--falsification-criterion", "x",
                "--exit-criterion", "x",
                "--final-oos-start", "2026-07-13",
                "--correlation-cache-dir", "/legacy/cache",
                *_temporal_cli_args(),
            ]
        )


def test_research_validate_file_rejects_contract_not_explicitly_eligible(tmp_path):
    trades = [_trade(date(2020, 1, 1))]
    contract = _verified_point_in_time_contract(trades, tmp_path)
    contract["eligible_for_final_validation"] = False
    source = tmp_path / "ineligible.json"
    source.write_text(
        json.dumps({"summary": {"research_data_contract": contract}, "qualified_trades": trades}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="audited-bound evidence bundle"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path",
                str(source),
                "--experiment-id",
                "ineligible-001",
                "--hypothesis",
                "Ineligible data is safe",
                "--expected-mechanism",
                "Unknown",
                "--falsification-criterion",
                "Contract is ineligible",
                "--exit-criterion",
                "Reject",
                "--final-oos-start",
                "2026-07-13",
                *_temporal_cli_args(end_date="2020-01-03"),
                "--ledger-path",
                str(tmp_path / "ineligible-ledger.jsonl"),
            ]
        )


def test_research_validate_file_records_aborted_interrupt(tmp_path, monkeypatch):
    trades = [_trade(date(2020, 1, 1))]
    source = tmp_path / "qualified.json"
    source.write_text(
        json.dumps(
            {
                "summary": {"research_data_contract": _point_in_time_contract(trades)},
                "qualified_trades": trades,
            }
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "aborted-ledger.jsonl"
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda summary, rows, **kwargs: _mock_validated_contract(summary),
    )
    monkeypatch.setattr(
        jobs,
        "run_frozen_strategy_validation",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path",
                str(source),
                "--experiment-id",
                "aborted-001",
                "--hypothesis",
                "Interrupted run is auditable",
                "--expected-mechanism",
                "N/A",
                "--falsification-criterion",
                "Interrupted",
                "--exit-criterion",
                "Record aborted",
                "--final-oos-start",
                "2026-07-13",
                *_temporal_cli_args(),
                "--ledger-path",
                str(ledger),
            ]
        )

    assert [row["event_type"] for row in read_experiment_ledger(str(ledger))] == [
        "registered",
        "aborted",
    ]


def test_validation_ledger_scrubs_paths_and_secrets_from_failure(tmp_path, monkeypatch):
    source = tmp_path / "qualified-secret.json"
    trades = [_trade(date(2020, 1, 1))]
    source.write_text(
        json.dumps(
            {
                "summary": {"research_data_contract": _point_in_time_contract(trades)},
                "qualified_trades": trades,
            }
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "scrubbed-ledger.jsonl"
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError(f"token=super-secret path={tmp_path}/private.json")
        ),
    )
    with pytest.raises(ValueError, match="super-secret"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path", str(source),
                "--experiment-id", "scrub-001",
                "--hypothesis", "x",
                "--expected-mechanism", "x",
                "--falsification-criterion", "x",
                "--exit-criterion", "x",
                "--final-oos-start", "2026-07-13",
                "--ledger-path", str(ledger),
                *_temporal_cli_args(),
            ]
        )
    events = read_experiment_ledger(str(ledger))
    serialized = json.dumps(events)
    assert "super-secret" not in serialized
    assert str(tmp_path) not in serialized
    assert events[0]["qualified_trades_artifact"] == {
        "basename": source.name,
        "sha256": None,
    }
    assert events[-1]["error_code"] == "VALIDATION_INPUT_REJECTED"
    assert events[-1]["message"] == "validation failed"


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), PermissionError("denied")])
def test_validation_read_failures_are_registered_then_failed(tmp_path, monkeypatch, error):
    source = tmp_path / "unreadable-qualified.json"
    ledger = tmp_path / "read-failure-ledger.jsonl"
    if isinstance(error, PermissionError):
        source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path", str(source),
                "--experiment-id", "read-failure",
                "--hypothesis", "x",
                "--expected-mechanism", "x",
                "--falsification-criterion", "x",
                "--exit-criterion", "x",
                "--final-oos-start", "2026-07-13",
                "--ledger-path", str(ledger),
                *_temporal_cli_args(),
            ]
        )
    events = read_experiment_ledger(str(ledger))
    assert [row["event_type"] for row in events] == ["registered", "failed"]
    assert events[0]["qualified_trades_artifact"] == {
        "basename": source.name,
        "sha256": None,
    }
    assert str(tmp_path) not in json.dumps(events)


def test_validation_oversize_payload_is_registered_then_failed(tmp_path):
    source = tmp_path / "oversize-qualified.json"
    with source.open("wb") as handle:
        handle.seek(64 * 1024 * 1024)
        handle.write(b"x")
    ledger = tmp_path / "oversize-ledger.jsonl"
    with pytest.raises(ValueError, match="size limit"):
        jobs.main(
            [
                "research-validate-file",
                "--qualified-trades-path", str(source),
                "--experiment-id", "oversize",
                "--hypothesis", "x",
                "--expected-mechanism", "x",
                "--falsification-criterion", "x",
                "--exit-criterion", "x",
                "--final-oos-start", "2026-07-13",
                "--ledger-path", str(ledger),
                *_temporal_cli_args(),
            ]
        )
    events = read_experiment_ledger(str(ledger))
    assert [row["event_type"] for row in events] == ["registered", "failed"]
    assert events[-1]["error_code"] == "VALIDATION_INPUT_REJECTED"
