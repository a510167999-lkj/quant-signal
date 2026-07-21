import json
from pathlib import Path

import pandas as pd
import pytest

from app import jobs
from app.artifact_native_evidence import (
    build_artifact_native_evidence,
    write_artifact_native_evidence,
)
from app.artifact_outcome_evidence import replay_trade_outcome
from app.indicators import add_indicators
from app.research_pit import verify_research_evidence_bundle
from app.research_pit_store import AuditedPointInTimeUniverse
from app.research_validation import (
    audited_authority_from_universe,
    qualified_trades_sha256,
    validate_point_in_time_contract,
)
from app.signals import evaluate_signal
from app.strategy_signal_evidence import build_signal_snapshot
from tests.test_research_pit import _write_verified_evidence
from tests.test_research_pit_store import _fully_resign_temporal_binding, _publish_two_day_bundle
from tests.test_research_stock_only_backtest import _publish_long_real_artifact


def _publish_four_day_temporal_bundle(tmp_path):
    temporal_contract_sha256 = "a" * 64
    artifact, audit, sessions = _publish_long_real_artifact(
        tmp_path,
        periods=65,
        temporal_contract_sha256=temporal_contract_sha256,
        temporal_role="development",
        breakout_position=62,
    )
    universe = AuditedPointInTimeUniverse.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        expected_artifact_root_sha256=artifact["artifact_root_sha256"],
        expected_temporal_contract_sha256=temporal_contract_sha256,
        expected_temporal_role="development",
    )
    assert sessions[-3] == universe.open_sessions(universe.start_date, universe.end_date)[
        -3
    ]
    return universe


def _complete_artifact_native_trade(universe):
    sessions = universe.open_sessions(universe.start_date, universe.end_date)
    signal_date, entry_date, exit_date = sessions[-3:]
    bars = universe.causal_signal_bars("600001", universe.start_date, signal_date)
    frame = pd.DataFrame(
        [
            {
                "date": row["trade_date"],
                "open": row["signal_open"],
                "high": row["signal_high"],
                "low": row["signal_low"],
                "close": row["signal_close"],
                "volume": row["volume_shares"],
            }
            for row in bars
        ]
    )
    signal = evaluate_signal(add_indicators(frame))
    assert signal["action"] == "BUY"
    trade = {
        "symbol": "600001",
        "signal_date": signal_date,
        "entry_date": entry_date,
        "planned_exit_date": exit_date,
        "exit_date": exit_date,
        "holding_days": 1,
        "exit_reason": "time_exit_next_open",
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
        "action": signal["action"],
        "entry_executability": universe.next_open_execution_evidence(
            "600001", entry_date, "buy"
        ),
        "exit_execution_evidence": universe.next_open_execution_evidence(
            "600001", exit_date, "sell"
        ),
        "strategy_signal": build_signal_snapshot(signal),
    }
    trade.update(replay_trade_outcome(universe, trade, compare_claim=False)["claim"])
    return trade


def test_strict_bundle_compiles_real_audited_artifact_into_development_validation(
    tmp_path,
):
    # This is deliberately non-mock: every membership, factor, execution and
    # signal/outcome claim is replayed from a signed read-only SQLite artifact.
    from app.research_pit import write_strict_research_evidence_bundle

    universe = _publish_four_day_temporal_bundle(tmp_path / "pit")
    evidence_root = tmp_path / "strict-evidence"
    evidence_root.mkdir()
    try:
        trade = _complete_artifact_native_trade(universe)
        qualified_path = evidence_root / "qualified.json"
        qualified_path.write_text(
            json.dumps(
                {"summary": {}, "qualified_trades": [trade]},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        native_payload = build_artifact_native_evidence(
            audited_universe=universe,
            qualified_trades=[trade],
            qualified_trades_path=str(qualified_path),
            artifact_root=str(evidence_root),
        )
        assert native_payload["eligibility"][
            "eligible_for_development_validation"
        ] is True
        native_descriptor = write_artifact_native_evidence(
            str(evidence_root), native_payload
        )
        strict_descriptor = write_strict_research_evidence_bundle(
            str(evidence_root),
            audited_universe=universe,
            artifact_native_evidence_path=native_descriptor["path"],
        )
        verified = verify_research_evidence_bundle(
            strict_descriptor["path"],
            audited_universe=universe,
            artifact_root=str(evidence_root),
        )
        authority = audited_authority_from_universe(universe)
        contract = {
            "schema_version": "research_data_contract/v2",
            "artifact_role": "development_only",
            "point_in_time": True,
            "eligible_for_development_validation": True,
            "eligible_for_final_validation": False,
            "final_oos_eligible": False,
            "entry_decision_cutoff": "next_open",
            "known_biases": [],
            "qualified_trades_sha256": qualified_trades_sha256([trade]),
            "qualified_trade_lineage_sha256": verified[
                "qualified_trade_lineage_sha256"
            ],
            "universe_sha256": verified["universe_sha256"],
            "calendar_sha256": verified["calendar_sha256"],
            "source_manifest_sha256": verified["source_manifest_sha256"],
            "artifact_root_sha256": universe.artifact_root_sha256,
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "temporal_contract_sha256": authority["temporal_contract_sha256"],
            "temporal_role": authority["temporal_role"],
            "audited_authority": authority,
            "artifact_manifest_sha256": authority["artifact_manifest_sha256"],
            "market_generation_root_sha256": authority[
                "market_generation_root_sha256"
            ],
            "stock_generation_lineage_sha256": authority[
                "stock_generation_lineage_sha256"
            ],
            "market_scope": verified["artifact_native_evidence"]["market_scope"],
            "evidence_bundle_path": Path(strict_descriptor["path"]).name,
            "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
        }

        validated = validate_point_in_time_contract(
            {"research_data_contract": contract},
            [trade],
            artifact_base_dir=str(evidence_root),
            declared_start_date=universe.start_date,
            declared_end_date=universe.end_date,
            audited_universe=universe,
        )

        assert validated["eligible_for_development_validation"] is True
        assert validated["eligible_for_final_validation"] is False
        assert validated["final_oos_eligible"] is False

        for field in (
            "artifact_root_sha256",
            "coverage_audit_sha256",
            "temporal_contract_sha256",
            "temporal_role",
            "artifact_manifest_sha256",
            "market_generation_root_sha256",
            "stock_generation_lineage_sha256",
        ):
            tampered = json.loads(json.dumps(contract))
            tampered[field] = "wrong" if field == "temporal_role" else "f" * 64
            with pytest.raises(ValueError, match="authority"):
                validate_point_in_time_contract(
                    {"research_data_contract": tampered},
                    [trade],
                    artifact_base_dir=str(evidence_root),
                    declared_start_date=universe.start_date,
                    declared_end_date=universe.end_date,
                    audited_universe=universe,
                )

        nested_tamper = json.loads(json.dumps(contract))
        nested_tamper["audited_authority"]["artifact_root_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="authority"):
            validate_point_in_time_contract(
                {"research_data_contract": nested_tamper},
                [trade],
                artifact_base_dir=str(evidence_root),
                declared_start_date=universe.start_date,
                declared_end_date=universe.end_date,
                audited_universe=universe,
            )

        extra_field = {**contract, "unverified_note": "ambiguous"}
        with pytest.raises(ValueError, match="fields"):
            validate_point_in_time_contract(
                {"research_data_contract": extra_field},
                [trade],
                artifact_base_dir=str(evidence_root),
                declared_start_date=universe.start_date,
                declared_end_date=universe.end_date,
                audited_universe=universe,
            )
        assert validated["verified_authority"]["temporal_role"] == "development"
    finally:
        universe.close()


def test_strict_bundle_cli_reports_development_only_not_live_proof(tmp_path, capsys):
    universe = _publish_four_day_temporal_bundle(tmp_path / "pit-cli")
    evidence_root = tmp_path / "strict-cli"
    evidence_root.mkdir()
    try:
        trade = _complete_artifact_native_trade(universe)
        qualified_path = evidence_root / "qualified.json"
        qualified_path.write_text(
            json.dumps({"qualified_trades": [trade]}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert jobs.main(
            [
                "research-build-artifact-native-evidence",
                "--qualified-trades-path",
                str(qualified_path),
                "--audited-pit-universe-path",
                str(universe.database_path),
                "--expected-coverage-audit-sha256",
                universe.coverage_audit_sha256,
                "--expected-artifact-root-sha256",
                universe.artifact_root_sha256,
                "--expected-temporal-contract-sha256",
                universe.temporal_contract_sha256,
                "--expected-temporal-role",
                "development",
                "--output-dir",
                str(evidence_root),
            ]
        ) == 0
        native_result = json.loads(capsys.readouterr().out)
        assert native_result["status"] == "development_native_evidence_complete"
        assert native_result["ready_for_strict_compilation"] is True
        assert native_result["strict_validation_eligible"] is False

        assert jobs.main(
            [
                "research-build-strict-evidence-bundle",
                "--artifact-native-evidence-path",
                native_result["evidence"]["path"],
                "--audited-pit-universe-path",
                str(universe.database_path),
                "--expected-coverage-audit-sha256",
                universe.coverage_audit_sha256,
                "--expected-artifact-root-sha256",
                universe.artifact_root_sha256,
                "--expected-temporal-contract-sha256",
                universe.temporal_contract_sha256,
                "--expected-temporal-role",
                "development",
                "--output-dir",
                str(evidence_root),
            ]
        ) == 0
        strict_result = json.loads(capsys.readouterr().out)
        compiled_path = Path(strict_result["compiled_qualified_trades"]["path"])
        compiled = json.loads(compiled_path.read_text(encoding="utf-8"))
        compiled_contract = compiled["summary"]["research_data_contract"]
        assert compiled_contract["eligible_for_development_validation"] is True
        assert compiled_contract["eligible_for_final_validation"] is False
        assert compiled_contract["final_oos_eligible"] is False
        assert compiled_contract["artifact_root_sha256"] == universe.artifact_root_sha256
        assert (
            compiled_contract["coverage_audit_sha256"]
            == universe.coverage_audit_sha256
        )
        assert (
            compiled_contract["temporal_contract_sha256"]
            == universe.temporal_contract_sha256
        )
        assert compiled_contract["temporal_role"] == "development"
        assert compiled_contract["audited_authority"] == strict_result[
            "audited_authority"
        ]
        validate_point_in_time_contract(
            compiled["summary"],
            compiled["qualified_trades"],
            artifact_base_dir=str(evidence_root),
            audited_universe=universe,
        )
    finally:
        universe.close()
    result = strict_result
    assert result["status"] == "development_validation_eligible"
    assert result["eligible_for_final_validation"] is False
    assert result["live_proof"] is False
    assert result["automatic_order_submission"] is False


def test_bound_audited_artifact_and_v2_evidence_validate_end_to_end(tmp_path):
    _store, audit, legacy = _publish_two_day_bundle(tmp_path / "store-fixture")
    temporal_contract_sha256 = "a" * 64
    binding = {
        "schema_version": "research-artifact-temporal-binding/v1",
        "contract_sha256": temporal_contract_sha256,
        "role": "development",
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "permitted_operation": "publish",
        "promotion_eligible": False,
    }
    database_path, manifest = _fully_resign_temporal_binding(
        Path(legacy["manifest_path"]), binding
    )
    authority = {
        "artifact_root_sha256": manifest["artifact_root_sha256"],
        "coverage_audit_sha256": manifest["coverage_audit_sha256"],
        "temporal_contract_sha256": temporal_contract_sha256,
        "temporal_role": "development",
        "artifact_manifest_sha256": manifest["manifest_sha256"],
        "market_generation_root_sha256": manifest["market_generations"]["root_sha256"],
        "stock_generation_lineage_sha256": manifest["stock_generation"]["lineage_sha256"],
    }
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = _write_verified_evidence(evidence_root, authority)
    verified = verify_research_evidence_bundle(evidence["path"])
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2024-01-02",
            "entry_date": "2024-01-03",
            "exit_date": "2024-01-03",
            "return_pct": 1.0,
            "max_adverse_pct": -0.5,
            "rank_score": 5.0,
        }
    ]
    contract = {
        "schema_version": "research_data_contract/v1",
        "artifact_role": "development_only",
        "point_in_time": True,
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "entry_decision_cutoff": "next_open",
        "known_biases": [],
        "qualified_trades_sha256": qualified_trades_sha256(trades),
        "universe_sha256": verified["universe_sha256"],
        "calendar_sha256": verified["calendar_sha256"],
        "source_manifest_sha256": verified["source_manifest_sha256"],
        "evidence_bundle_path": Path(evidence["path"]).name,
        "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
    }
    universe = AuditedPointInTimeUniverse.from_file(
        str(database_path),
        expected_coverage_audit_sha256=authority["coverage_audit_sha256"],
        expected_artifact_root_sha256=authority["artifact_root_sha256"],
        expected_temporal_contract_sha256=temporal_contract_sha256,
        expected_temporal_role="development",
    )
    try:
        with pytest.raises(ValueError, match="eligibility reasons"):
            validate_point_in_time_contract(
                {"research_data_contract": contract},
                trades,
                artifact_base_dir=str(evidence_root),
                declared_start_date="2024-01-02",
                declared_end_date="2024-01-03",
                audited_universe=universe,
            )
    finally:
        universe.close()
