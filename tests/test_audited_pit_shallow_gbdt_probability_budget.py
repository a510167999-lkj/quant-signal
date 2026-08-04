from copy import deepcopy
from datetime import date, timedelta

import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt_probability_budget as budget
from app.audited_pit_score_contract import SHALLOW_GBDT_SCORE_CONTRACT


def _trade(
    security_id: str,
    probability: float,
    *,
    entry_date: str = "2025-01-03",
    exit_date: str = "2025-01-10",
) -> dict[str, object]:
    return {
        "candidate_key": f"{security_id}|2025-01-02",
        "security_id": security_id,
        "signal_date": "2025-01-02",
        "entry_date": entry_date,
        "exit_date": exit_date,
        "predicted_positive_utility_probability": probability,
    }


def test_probability_budget_uses_unrounded_probability_and_leaves_cash_unallocated() -> None:
    allocated, receipt = budget.allocate_shallow_gbdt_probability_budget(
        [
            _trade("cn-a-share:000001.SZ", 0.500000001),
            _trade("cn-a-share:000002.SZ", 0.75),
            _trade("cn-a-share:000003.SZ", 1.0),
        ],
        max_active_positions=3,
        exposure_multiplier=1.0,
    )

    assert allocated[0]["position_budget_fraction"] == pytest.approx(
        2e-9 / 3
    )
    assert allocated[1]["position_budget_fraction"] == pytest.approx(1 / 6)
    assert allocated[2]["position_budget_fraction"] == pytest.approx(1 / 3)
    assert receipt["allocation"]["cash_policy"] == (
        "unallocated_cash_remains_cash"
    )
    assert receipt["allocation"]["renormalize_across_positions"] is False
    assert budget.verify_shallow_gbdt_probability_budget_receipt(
        allocated,
        receipt,
        max_active_positions=3,
        exposure_multiplier=1.0,
    )["verified"] is True


def test_probability_budget_rejects_nonpositive_probability_gate_and_preloaded_budget() -> None:
    with pytest.raises(ValueError, match="strict positive probability gate"):
        budget.allocate_shallow_gbdt_probability_budget(
            [_trade("cn-a-share:000001.SZ", 0.5)],
            max_active_positions=3,
            exposure_multiplier=1.0,
        )

    trade = _trade("cn-a-share:000001.SZ", 0.75)
    trade["position_budget_fraction"] = 0.2
    with pytest.raises(ValueError, match="must not be preloaded"):
        budget.allocate_shallow_gbdt_probability_budget(
            [trade],
            max_active_positions=3,
            exposure_multiplier=1.0,
        )

    class EquivalentFloat(float):
        pass

    allocation_spec = deepcopy(dict(budget.PROBABILITY_BUDGET_ALLOCATION_SPEC))
    allocation_spec["score_contract"]["value"] = EquivalentFloat(0.5)
    with pytest.raises(ValueError, match="allocation spec is not frozen"):
        budget.allocate_shallow_gbdt_probability_budget(
            [_trade("cn-a-share:000001.SZ", 0.75)],
            max_active_positions=3,
            exposure_multiplier=1.0,
            allocation_spec=allocation_spec,
        )


def test_probability_budget_fails_closed_on_capacity_or_receipt_tampering() -> None:
    trades = [
        _trade("cn-a-share:000001.SZ", 0.75),
        _trade("cn-a-share:000002.SZ", 0.75),
        _trade("cn-a-share:000003.SZ", 0.75),
        _trade("cn-a-share:000004.SZ", 0.75),
    ]
    with pytest.raises(ValueError, match="active capacity"):
        budget.allocate_shallow_gbdt_probability_budget(
            trades,
            max_active_positions=3,
            exposure_multiplier=1.0,
        )

    allocated, receipt = budget.allocate_shallow_gbdt_probability_budget(
        [_trade("cn-a-share:000001.SZ", 0.75)],
        max_active_positions=3,
        exposure_multiplier=1.0,
    )
    tampered = deepcopy(allocated)
    tampered[0]["position_budget_fraction"] = 0.2
    with pytest.raises(ValueError, match="allocation differs"):
        budget.verify_shallow_gbdt_probability_budget_receipt(
            tampered,
            receipt,
            max_active_positions=3,
            exposure_multiplier=1.0,
        )


def _evaluation_sessions() -> list[str]:
    start = date(2025, 1, 2)
    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range(366)
    ]


def _passing_metrics() -> dict[str, object]:
    return {
        "gate_metric_basis": "unrounded_float64",
        "trade_win_rate_pct_raw": 60.0,
        "portfolio_max_drawdown_pct_raw": -10.0,
        "trade_profit_factor_raw": 2.0,
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct_raw": 60.0,
        "calmar_latest_12m_raw": 2.0,
        "rolling_1y_windows": [
            {
                "return_pct_raw": 60.0,
                "max_drawdown_pct_raw": -10.0,
                "payoff_ratio_raw": 2.0,
                "profit_factor_raw": 2.0,
                "calmar_raw": 2.0,
            }
        ],
    }


def test_fixed_oof_probability_budget_is_opt_in_and_receipted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[dict[str, object]]] = []

    def fake_trade_metrics(selected, *args, **kwargs):
        captured.append([dict(item) for item in selected])
        return _passing_metrics()

    monkeypatch.setattr(ridge, "_trade_metrics", fake_trade_metrics)
    strategy = deepcopy(ridge.CONTINUOUS_RIDGE_OOF_SPEC)
    strategy["signal_tag"] = "shallow_gbdt_probability_budget"
    strategy["advancement_thresholds"]["minimum_complete_trades"] = 1
    strategy["selection"]["position_budget_allocation"] = dict(
        budget.PROBABILITY_BUDGET_ALLOCATION_SPEC
    )
    sweep, _ = ridge._evaluate_fixed_oof(
        [
            {
                **_trade("cn-a-share:000001.SZ", 0.75),
                "symbol": "000001",
                "ts_code": "000001.SZ",
                "signal_industry": "industry-a",
                "candidate_amount": 100.0,
                "right_censored": False,
                "score": 0.75,
                "rank_score": 0.75,
            }
        ],
        rank_mode="positive_utility_probability",
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=strategy,
        sweep_schema_version=(
            "strict-ranked-liquidity-shallow-gbdt-probability-budget-"
            "fixed-oof/v1"
        ),
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert captured[0][0]["position_budget_fraction"] == pytest.approx(1 / 6)
    allocation_receipt = sweep["top"][0]["position_budget_allocation_receipt"]
    assert allocation_receipt["allocation_rows"][0][
        "position_budget_fraction"
    ] == pytest.approx(1 / 6)


def test_probability_budget_control_rejects_preloaded_main_allocation() -> None:
    selected = [_trade("cn-a-share:000001.SZ", 0.75)]
    selected[0]["position_budget_fraction"] = 0.2

    with pytest.raises(ValueError, match="control must not be preloaded"):
        ridge._selected_for_fixed_oof_metrics(
            selected,
            strategy_spec=budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC,
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
            apply_position_budget=False,
        )


def test_probability_budget_hypothesis_holds_selection_constant_for_its_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[dict[str, object]]] = []

    def fake_trade_metrics(selected, *args, **kwargs):
        captured.append([dict(item) for item in selected])
        return _passing_metrics()

    monkeypatch.setattr(ridge, "_trade_metrics", fake_trade_metrics)
    candidate = {
        **_trade("cn-a-share:000001.SZ", 0.75),
        "symbol": "000001",
        "ts_code": "000001.SZ",
        "signal_industry": "industry-a",
        "candidate_amount": 100.0,
        "right_censored": False,
        "score": 0.75,
        "rank_score": 0.75,
    }

    evaluation = budget.evaluate_shallow_gbdt_probability_budget_hypothesis(
        [candidate],
        evaluation_session_dates=_evaluation_sessions(),
    )

    assert captured[0][0]["position_budget_fraction"] == pytest.approx(1 / 6)
    assert "position_budget_fraction" not in captured[1][0]
    assert evaluation["main_selection_receipt"] == evaluation[
        "equal_weight_control_selection_receipt"
    ]
    assert evaluation["comparison"]["same_selected_trade_keys"] is True


def test_probability_budget_hypothesis_rejects_any_frozen_strategy_drift() -> None:
    strategy = deepcopy(budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC)
    strategy["selection"]["position_budget_control"] = "renormalize"

    with pytest.raises(ValueError, match="strategy is not frozen"):
        budget.evaluate_shallow_gbdt_probability_budget_hypothesis(
            [],
            evaluation_session_dates=_evaluation_sessions(),
            strategy_spec=strategy,
        )
