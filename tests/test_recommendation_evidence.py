import hashlib
import json

from app.recommendation_evidence import (
    build_profile_evidence_receipt,
    verify_profile_evidence_receipt,
)
from app.recommendation_profile import DEFAULT_PROFILE, profile_to_dict


def _report(metrics=None):
    aggregate = {
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct": 52.0,
        "portfolio_max_drawdown_pct": -12.0,
        "selected_trade_count": 240,
        "signal_days": 132,
        "trade_win_count": 134,
        "trade_nonwin_count": 106,
        "trade_win_rate_pct": 55.83,
        "win_rate_wilson_95_lower_pct": 49.5,
        "win_rate_wilson_95_upper_pct": 61.9,
        "trade_payoff_ratio": 1.45,
        "trade_profit_factor": 1.6,
        "calmar_latest_12m": 2.1,
        "capital_model": "slot-daily",
        "exposure_multiplier": 1.0,
        "max_active_positions": 3,
        "hold_days": 5,
        "annual_financing_rate_pct": 8.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
    }
    if metrics:
        aggregate.update(metrics)
    return {
        "aggregate_validation": aggregate,
        "qualification": {
            "completion_pass": False,
            "all_rolling_12m_stability_pass": True,
        },
        "dataset_sha256": "dataset-hash",
        "strategy_sha256": "strategy-hash",
        "validation_sha256": "validation-hash",
    }


def _rolling_windows():
    return {
        "window_days": 365,
        "window_count": 2,
        "windows": [
            {
                "start_date": "2024-01-01",
                "end_date": "2025-01-01",
                "return_pct": 51.0,
                "max_drawdown_pct": -12.0,
                "payoff_ratio": 1.4,
                "profit_factor": 1.5,
                "calmar": 1.8,
            },
            {
                "start_date": "2024-07-01",
                "end_date": "2025-07-01",
                "return_pct": 52.0,
                "max_drawdown_pct": -11.0,
                "payoff_ratio": 1.45,
                "profit_factor": 1.6,
                "calmar": 2.1,
            },
        ],
    }


def _evidence():
    return {
        "pit_contract": True,
        "temporal_contract": True,
        "cost_slippage": True,
        "artifact_execution": True,
        "strategy_signal_replay": True,
        "strategy_entry_decision": True,
        "strategy_selection_replay": True,
        "outcome_replay": True,
        "double_cost": True,
        "regime": True,
        "final_oos": False,
        "shadow": False,
        "live_monitoring": False,
        "pit_verified": True,
    }


def test_receipt_is_incomplete_when_rolling_or_pit_evidence_is_missing():
    receipt = build_profile_evidence_receipt(
        profile=DEFAULT_PROFILE,
        experiment_id="auto-031",
        strategy={"capital_model": "slot-daily"},
        validation={"final_oos_start": "2026-07-13"},
        validation_report=_report({"signal_days": 107}),
        rolling_12m={"window_days": 365, "window_count": 0, "windows": []},
        evidence={"pit_contract": False, "temporal_contract": False, "cost_slippage": True},
        source_artifact={"path": "cache.json", "sha256": "cache-hash"},
        report_artifact={"path": "report.json", "sha256": "report-hash"},
        ledger_anchor={"sequence": 106, "record_hash": "ledger-hash"},
    )

    assert receipt["status"] == "incomplete"
    assert receipt["live_proof"] is False
    assert receipt["auto_order"] is False
    assert "all_rolling_12m" in receipt["blocking_gates"]
    assert "pit_contract" in receipt["blocking_gates"]
    assert "signal_days_120" in receipt["blocking_gates"]
    assert len(receipt["receipt_sha256"]) == 64
    assert receipt["profile_hash"] == profile_to_dict(DEFAULT_PROFILE)["profile_hash"]


def test_receipt_requires_entry_decision_and_portfolio_selection_replay():
    evidence = _evidence()
    evidence.pop("strategy_entry_decision")
    evidence.pop("strategy_selection_replay")
    receipt = build_profile_evidence_receipt(
        profile=DEFAULT_PROFILE,
        experiment_id="selection-unbound",
        strategy={"capital_model": "slot-daily"},
        validation={"final_oos_start": "2026-07-13"},
        validation_report=_report({"win_rate_wilson_95_lower_pct": 52.2}),
        rolling_12m=_rolling_windows(),
        evidence=evidence,
        source_artifact={"path": "cache.json", "sha256": "cache-hash"},
        report_artifact={"path": "report.json", "sha256": "report-hash"},
        ledger_anchor={"sequence": 106, "record_hash": "ledger-hash"},
    )

    assert receipt["status"] == "incomplete"
    assert "strategy_entry_decision" in receipt["blocking_gates"]
    assert "strategy_selection_replay" in receipt["blocking_gates"]


def test_receipt_qualifies_development_evidence_but_not_live_proof():
    receipt = build_profile_evidence_receipt(
        profile=DEFAULT_PROFILE,
        experiment_id="candidate-valid",
        strategy={"capital_model": "slot-daily"},
        validation={"final_oos_start": "2026-07-13"},
        validation_report=_report({"win_rate_wilson_95_lower_pct": 52.2}),
        rolling_12m=_rolling_windows(),
        evidence=_evidence(),
        source_artifact={"path": "cache.json", "sha256": "cache-hash"},
        report_artifact={"path": "report.json", "sha256": "report-hash"},
        ledger_anchor={"sequence": 106, "record_hash": "ledger-hash"},
    )

    assert receipt["status"] == "qualified"
    assert receipt["evidence_scope"] == "development_only"
    assert receipt["live_proof"] is False
    assert receipt["blocking_gates"] == ["final_oos", "shadow", "live_monitoring"]
    assert receipt["metrics"]["calmar"] == 2.1
    assert receipt["metric_basis"]["calmar"] == "rolling_365d_return_over_same_window_drawdown"


def test_receipt_requires_authoritative_rolling_stability_claim():
    report = _report()
    report["qualification"]["all_rolling_12m_stability_pass"] = False
    receipt = build_profile_evidence_receipt(
        profile=DEFAULT_PROFILE,
        experiment_id="stability-unbound",
        strategy={"capital_model": "slot-daily"},
        validation={"final_oos_start": "2026-07-13"},
        validation_report=report,
        rolling_12m=_rolling_windows(),
        evidence=_evidence(),
        source_artifact={"path": "cache.json", "sha256": "cache-hash"},
        report_artifact={"path": "report.json", "sha256": "report-hash"},
        ledger_anchor={"sequence": 106, "record_hash": "ledger-hash"},
    )

    assert receipt["status"] == "incomplete"
    assert receipt["metrics"]["rolling_12m_stability_pass"] is False
    assert "all_rolling_12m_stability" in receipt["blocking_gates"]


def test_receipt_verifier_checks_canonical_hash_and_source_artifacts(tmp_path):
    source = tmp_path / "qualified.json"
    report = tmp_path / "report.json"
    source.write_text("qualified", encoding="utf-8")
    report.write_text("report", encoding="utf-8")
    receipt = build_profile_evidence_receipt(
        profile=DEFAULT_PROFILE,
        experiment_id="candidate-valid",
        strategy={"capital_model": "slot-daily"},
        validation={"final_oos_start": "2026-07-13"},
        validation_report=_report({"win_rate_wilson_95_lower_pct": 52.2}),
        rolling_12m=_rolling_windows(),
        evidence=_evidence(),
        source_artifact={
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        report_artifact={
            "path": str(report),
            "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        },
        ledger_anchor={"sequence": 106, "record_hash": "ledger-hash"},
    )

    verified = verify_profile_evidence_receipt(receipt)
    assert verified["ok"] is True

    tampered = dict(receipt)
    tampered["metrics"] = dict(receipt["metrics"], annualized_return_pct=999.0)
    assert verify_profile_evidence_receipt(tampered)["ok"] is False

    coherently_rehashed = dict(receipt)
    coherently_rehashed["metrics"] = dict(
        receipt["metrics"], annualized_return_pct=10.0
    )
    coherently_rehashed["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in coherently_rehashed.items()
                if key != "receipt_sha256"
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        .encode("utf-8")
    ).hexdigest()
    verified = verify_profile_evidence_receipt(coherently_rehashed)
    assert verified["ok"] is False
    assert "gates_mismatch" in verified["errors"]
