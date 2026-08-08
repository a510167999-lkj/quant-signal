from copy import deepcopy
from datetime import date, timedelta

import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as shallow_gbdt
from app import audited_pit_shallow_gbdt_risk_on_breadth as risk_on_breadth
from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
)


def _candidate(*, probability: bool) -> dict[str, object]:
    score = 0.75 if probability else 1.25
    candidate: dict[str, object] = {
        "candidate_key": "cn-a-share:000001.SZ|2025-01-02",
        "symbol": "000001",
        "ts_code": "000001.SZ",
        "security_id": "cn-a-share:000001.SZ",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-10",
        "signal_industry": "industry-a",
        "candidate_amount": 200.0,
        "right_censored": False,
        "score": score,
        "rank_score": score,
    }
    if probability:
        candidate["predicted_positive_utility_probability"] = score
    else:
        candidate["predicted_net_return_pct"] = score
    return candidate


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


def _strategy(*, probability: bool) -> dict[str, object]:
    strategy = deepcopy(ridge.CONTINUOUS_RIDGE_OOF_SPEC)
    strategy["advancement_thresholds"]["minimum_complete_trades"] = 1
    if probability:
        strategy["signal_tag"] = "shallow_gbdt_utility_logit"
    return strategy


def test_fixed_oof_sweep_schema_is_bound_to_the_frozen_score_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ridge, "_trade_metrics", lambda *args, **kwargs: _passing_metrics())
    ridge_candidate = _candidate(probability=False)
    gbdt_candidate = _candidate(probability=True)
    ridge_strategy = _strategy(probability=False)
    gbdt_strategy = _strategy(probability=True)

    legacy_ridge, _ = ridge._evaluate_fixed_oof(
        [ridge_candidate],
        rank_mode="predicted_net_return",
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=ridge_strategy,
    )
    ridge_v3, _ = ridge._evaluate_fixed_oof(
        [ridge_candidate],
        rank_mode="predicted_net_return",
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=ridge_strategy,
        sweep_schema_version="strict-ranked-liquidity-ridge-fixed-oof/v3",
        score_contract=RIDGE_SCORE_CONTRACT,
    )

    assert legacy_ridge["schema_version"] == (
        "strict-ranked-liquidity-ridge-fixed-oof/v2"
    )
    assert ridge_v3["schema_version"] == (
        "strict-ranked-liquidity-ridge-fixed-oof/v3"
    )

    for invalid_schema in (
        "strict-ranked-liquidity-ridge-fixed-oof/v2",
        "strict-ranked-liquidity-ridge-fixed-oof/v3",
        "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v9",
    ):
        with pytest.raises(ValueError, match="sweep schema"):
            ridge._evaluate_fixed_oof(
                [gbdt_candidate],
                rank_mode="positive_utility_probability",
                evaluation_session_dates=_evaluation_sessions(),
                strategy_spec=gbdt_strategy,
                sweep_schema_version=invalid_schema,
                score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
            )


def test_risk_on_breadth_sweep_schema_is_bound_only_to_its_frozen_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ridge,
        "_trade_metrics",
        lambda *args, **kwargs: _passing_metrics(),
    )
    candidate = _candidate(probability=True)
    candidate["cross_section_above_ma20_fraction"] = 0.75
    risk_schema = (
        "strict-ranked-liquidity-shallow-gbdt-risk-on-breadth-fixed-oof/v1"
    )
    base_schema = "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"

    sweep, _ = ridge._evaluate_fixed_oof(
        [candidate],
        rank_mode="positive_utility_probability",
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=risk_on_breadth.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        sweep_schema_version=risk_schema,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    assert sweep["schema_version"] == risk_schema

    with pytest.raises(ValueError, match="sweep schema"):
        ridge._evaluate_fixed_oof(
            [candidate],
            rank_mode="positive_utility_probability",
            evaluation_session_dates=_evaluation_sessions(),
            strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
            sweep_schema_version=risk_schema,
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )
    with pytest.raises(ValueError, match="sweep schema"):
        ridge._evaluate_fixed_oof(
            [candidate],
            rank_mode="positive_utility_probability",
            evaluation_session_dates=_evaluation_sessions(),
            strategy_spec=(
                risk_on_breadth.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC
            ),
            sweep_schema_version=base_schema,
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


@pytest.mark.parametrize(
    ("probability", "rank_mode", "replacement_schema"),
    [
        (
            False,
            "predicted_net_return",
            "shallow-gbdt-industry-selection-receipt/v1",
        ),
        (
            True,
            "positive_utility_probability",
            "continuous-ridge-industry-selection-receipt/v1",
        ),
    ],
)
def test_recompute_fixed_oof_gate_rejects_cross_contract_selection_schema(
    monkeypatch: pytest.MonkeyPatch,
    probability: bool,
    rank_mode: str,
    replacement_schema: str,
) -> None:
    monkeypatch.setattr(ridge, "_trade_metrics", lambda *args, **kwargs: _passing_metrics())
    candidate = _candidate(probability=probability)
    contract = (
        SHALLOW_GBDT_SCORE_CONTRACT if probability else RIDGE_SCORE_CONTRACT
    )
    schema = (
        "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
        if probability
        else "strict-ranked-liquidity-ridge-fixed-oof/v3"
    )
    strategy = _strategy(probability=probability)
    sweep, receipt = ridge._evaluate_fixed_oof(
        [candidate],
        rank_mode=rank_mode,
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=strategy,
        sweep_schema_version=schema,
        score_contract=contract,
    )
    tampered_receipt = dict(receipt)
    tampered_receipt["schema_version"] = replacement_schema
    candidate_table = ridge._selection_candidate_table(
        [candidate],
        score_contract=contract,
    )

    with pytest.raises(ValueError, match="selection receipt schema"):
        ridge._recompute_fixed_oof_gate(
            sweep,
            tampered_receipt,
            candidate_table,
            strategy_spec=strategy,
            rank_mode=rank_mode,
            score_contract=contract,
        )
