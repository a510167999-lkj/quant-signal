from app.recommendation_gate import evaluate_recommendation_gate
from app.recommendation_profile import DEFAULT_PROFILE


def _metrics():
    return {
        "annualized_return_pct": 52.0,
        "max_drawdown_pct": 12.0,
        "win_rate_pct": 56.0,
        "win_rate_wilson_lower_pct": 52.1,
        "payoff_ratio": 1.45,
        "profit_factor": 1.6,
        "calmar": 2.0,
        "signal_days": 132,
        "rolling_12m": [
            {
                "annualized_return_pct": 50.2,
                "max_drawdown_pct": 13.0,
                "payoff_ratio": 1.4,
                "profit_factor": 1.5,
                "calmar": 1.8,
            }
        ],
    }


def _evidence():
    return {
        "pit_contract": True,
        "temporal_contract": True,
        "final_oos": False,
        "cost_slippage": True,
        "artifact_execution": True,
        "strategy_signal_replay": True,
        "outcome_replay": True,
    }


def test_development_candidate_passes_metrics_but_is_not_live_proof():
    result = evaluate_recommendation_gate(DEFAULT_PROFILE, _metrics(), _evidence(), {"ok": True})

    assert result["development_ready"] is True
    assert result["live_proof"] is False
    assert result["evidence_scope"] == "development_only"
    assert result["reasons"] == []
    assert result["auto_order"] is False


def test_missing_pit_evidence_fails_closed_and_blocks_buy():
    evidence = _evidence()
    evidence["pit_contract"] = False

    result = evaluate_recommendation_gate(DEFAULT_PROFILE, _metrics(), evidence, {"ok": True})

    assert result["development_ready"] is False
    assert "pit_contract_missing" in result["reasons"]
    assert result["live_proof"] is False


def test_missing_artifact_replay_evidence_fails_closed():
    evidence = _evidence()
    evidence.update(
        {
            "artifact_execution": True,
            "strategy_signal_replay": False,
            "outcome_replay": True,
        }
    )

    result = evaluate_recommendation_gate(DEFAULT_PROFILE, _metrics(), evidence, {"ok": True})

    assert result["development_ready"] is False
    assert "strategy_signal_replay_missing" in result["reasons"]


def test_metric_or_health_failure_is_reported_as_structured_reasons():
    metrics = _metrics()
    metrics["profit_factor"] = 1.1
    metrics["signal_days"] = 107

    result = evaluate_recommendation_gate(
        DEFAULT_PROFILE,
        metrics,
        _evidence(),
        {"ok": False, "reasons": ["provider_degraded"]},
    )

    assert result["development_ready"] is False
    assert "profit_factor_below_min" in result["reasons"]
    assert "signal_days_below_min" in result["reasons"]
    assert "production_health_not_ok" in result["reasons"]
    assert result["auto_order"] is False
