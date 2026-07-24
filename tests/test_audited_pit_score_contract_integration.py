from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
import math

import numpy as np
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
    candidate_score,
)


GBDT_SCORE_EVIDENCE_COLUMNS = (
    "candidate_key",
    "signal_date",
    "trade_key",
    "security_id",
    "signal_industry",
    "predicted_positive_utility_probability",
    "candidate_amount",
    "right_censored",
    "outcome_payload_sha256",
    "candidate_payload_sha256",
)

GBDT_CANDIDATE_TABLE_COLUMNS = {
    "trade_key",
    "candidate_key",
    "security_id",
    "signal_industry",
    "predicted_positive_utility_probability",
    "candidate_amount",
    "right_censored",
}


def _candidate(
    security_id: str,
    *,
    amount: float,
    industry: str,
    score: float,
    gbdt: bool,
) -> dict[str, object]:
    symbol = security_id.split(":")[-1].split(".")[0]
    candidate: dict[str, object] = {
        "candidate_key": f"{security_id}|2025-01-02",
        "symbol": symbol,
        "ts_code": security_id.split(":")[-1],
        "security_id": security_id,
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-10",
        "signal_industry": industry,
        "candidate_amount": amount,
        "right_censored": False,
        "execution_evidence": {
            "strict_next_open_fill": True,
            "source": {"schema_version": "test-execution-evidence/v1"},
        },
        "score": score,
        "rank_score": score,
    }
    if gbdt:
        candidate["predicted_positive_utility_probability"] = score
    else:
        candidate["predicted_net_return_pct"] = score
    return candidate


def _assert_no_key_recursive(value: object, forbidden: str) -> None:
    if isinstance(value, Mapping):
        assert forbidden not in value
        for nested in value.values():
            _assert_no_key_recursive(nested, forbidden)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for nested in value:
            _assert_no_key_recursive(nested, forbidden)


def test_explicit_ridge_contract_is_byte_for_byte_legacy_compatible() -> None:
    candidates = [
        _candidate(
            "cn-a-share:000001.SZ",
            amount=200.0,
            industry="industry-a",
            score=1.25,
            gbdt=False,
        ),
        _candidate(
            "cn-a-share:000002.SZ",
            amount=300.0,
            industry="industry-b",
            score=-0.25,
            gbdt=False,
        ),
    ]

    legacy_outcome = ridge._outcome_payload_from_scored_candidate(candidates[0])
    explicit_outcome = ridge._outcome_payload_from_scored_candidate(
        candidates[0],
        score_contract=RIDGE_SCORE_CONTRACT,
    )
    assert explicit_outcome == legacy_outcome

    legacy_evidence = ridge._compact_scored_execution_evidence(candidates)
    explicit_evidence = ridge._compact_scored_execution_evidence(
        candidates,
        score_contract=RIDGE_SCORE_CONTRACT,
    )
    assert explicit_evidence == legacy_evidence
    assert legacy_evidence["schema_version"] == "ranked-liquidity-score-evidence/v3"
    assert legacy_evidence["columns"] == list(
        ridge.SCORED_EXECUTION_EVIDENCE_COLUMNS
    )
    assert explicit_evidence["receipt_sha256"] == legacy_evidence["receipt_sha256"]

    legacy_pool, legacy_pool_receipt = ridge._positive_score_pool(candidates)
    explicit_pool, explicit_pool_receipt = ridge._positive_score_pool(
        candidates,
        score_contract=RIDGE_SCORE_CONTRACT,
    )
    assert explicit_pool == legacy_pool
    assert explicit_pool_receipt == legacy_pool_receipt
    assert legacy_pool_receipt["schema_version"] == (
        "continuous-ridge-positive-score-pool/v1"
    )
    assert legacy_pool_receipt["comparison"] == (
        "predicted_net_return_pct_strictly_greater_than_zero"
    )
    assert explicit_pool_receipt["receipt_sha256"] == (
        legacy_pool_receipt["receipt_sha256"]
    )

    assert ridge._selection_candidate_table(
        legacy_pool,
        score_contract=RIDGE_SCORE_CONTRACT,
    ) == ridge._selection_candidate_table(legacy_pool)
    assert ridge._selection_rank_key(
        legacy_pool[0],
        rank_mode="predicted_net_return",
        score_contract=RIDGE_SCORE_CONTRACT,
    ) == ridge._selection_rank_key(
        legacy_pool[0],
        rank_mode="predicted_net_return",
    )

    legacy_selected, legacy_selection = (
        ridge._select_with_industry_cap_receipt(
            legacy_pool,
            rank_mode="predicted_net_return",
            top_n=3,
            max_active_positions=3,
        )
    )
    explicit_selected, explicit_selection = (
        ridge._select_with_industry_cap_receipt(
            legacy_pool,
            rank_mode="predicted_net_return",
            top_n=3,
            max_active_positions=3,
            score_contract=RIDGE_SCORE_CONTRACT,
        )
    )
    assert explicit_selected == legacy_selected
    assert explicit_selection == legacy_selection
    assert legacy_selection["schema_version"] == (
        "continuous-ridge-industry-selection-receipt/v1"
    )
    assert legacy_selection["parameters"]["rank_mode"] == "predicted_net_return"
    assert explicit_selection["receipt_sha256"] == (
        legacy_selection["receipt_sha256"]
    )


def test_gbdt_positive_pool_uses_the_exact_strict_probability_boundary() -> None:
    below = np.nextafter(0.5, -math.inf)
    above = np.nextafter(0.5, math.inf)
    candidates = [
        _candidate(
            "cn-a-share:000001.SZ",
            amount=300.0,
            industry="industry-a",
            score=float(below),
            gbdt=True,
        ),
        _candidate(
            "cn-a-share:000002.SZ",
            amount=200.0,
            industry="industry-b",
            score=0.5,
            gbdt=True,
        ),
        _candidate(
            "cn-a-share:000003.SZ",
            amount=100.0,
            industry="industry-c",
            score=float(above),
            gbdt=True,
        ),
    ]

    pool, receipt = ridge._positive_score_pool(
        candidates,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert [candidate["security_id"] for candidate in pool] == [
        "cn-a-share:000003.SZ"
    ]
    assert receipt["schema_version"] == (
        "shallow-gbdt-positive-utility-pool/v1"
    )
    assert receipt["comparison"] == (
        "predicted_positive_utility_probability_strictly_greater_than_0.5"
    )
    assert receipt["input_candidate_count"] == 3
    assert receipt["positive_candidate_count"] == 1
    assert receipt["receipt_sha256"] == ridge._sha256(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
    )
    _assert_no_key_recursive((pool, receipt), "predicted_net_return_pct")


def test_gbdt_score_evidence_hashes_score_free_outcomes_and_probability_rows() -> None:
    candidate = _candidate(
        "cn-a-share:000001.SZ",
        amount=300.0,
        industry="industry-a",
        score=0.75,
        gbdt=True,
    )
    original = deepcopy(candidate)

    outcome = ridge._outcome_payload_from_scored_candidate(
        candidate,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    evidence = ridge._compact_scored_execution_evidence(
        [candidate],
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert candidate == original
    assert outcome == {
        key: value
        for key, value in candidate.items()
        if key
        not in {
            "predicted_positive_utility_probability",
            "score",
            "rank_score",
        }
    }
    assert evidence["schema_version"] == (
        "ranked-liquidity-shallow-gbdt-score-evidence/v1"
    )
    assert evidence["columns"] == list(GBDT_SCORE_EVIDENCE_COLUMNS)
    row = dict(zip(evidence["columns"], evidence["rows"][0]))
    assert row["predicted_positive_utility_probability"] == pytest.approx(0.75)
    assert row["outcome_payload_sha256"] == ridge._sha256(outcome)
    assert row["candidate_payload_sha256"] == ridge._sha256(candidate)
    assert evidence["receipt_sha256"] == ridge._sha256(
        {
            key: value
            for key, value in evidence.items()
            if key != "receipt_sha256"
        }
    )
    _assert_no_key_recursive(
        (outcome, evidence),
        "predicted_net_return_pct",
    )


def test_gbdt_candidate_table_and_receipts_use_probability_contract() -> None:
    candidates = [
        _candidate(
            "cn-a-share:000003.SZ",
            amount=200.0,
            industry="industry-c",
            score=0.70,
            gbdt=True,
        ),
        _candidate(
            "cn-a-share:000002.SZ",
            amount=100.0,
            industry="industry-b",
            score=0.80,
            gbdt=True,
        ),
        _candidate(
            "cn-a-share:000001.SZ",
            amount=100.0,
            industry="industry-a",
            score=0.80,
            gbdt=True,
        ),
    ]

    table = ridge._selection_candidate_table(
        candidates,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    assert all(set(row) == GBDT_CANDIDATE_TABLE_COLUMNS for row in table)
    assert [row["security_id"] for row in table] == [
        "cn-a-share:000001.SZ",
        "cn-a-share:000002.SZ",
        "cn-a-share:000003.SZ",
    ]
    assert ridge._selection_rank_key(
        candidates[1],
        rank_mode="positive_utility_probability",
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    ) == (
        -0.80,
        -100.0,
        "cn-a-share:000002.SZ",
    )

    main, main_receipt = ridge._select_with_industry_cap_receipt(
        candidates,
        rank_mode="positive_utility_probability",
        top_n=3,
        max_active_positions=3,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    baseline, baseline_receipt = ridge._select_with_industry_cap_receipt(
        candidates,
        rank_mode="signal_date_amount",
        top_n=3,
        max_active_positions=3,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert [candidate["security_id"] for candidate in main] == [
        "cn-a-share:000001.SZ",
        "cn-a-share:000002.SZ",
        "cn-a-share:000003.SZ",
    ]
    assert [candidate["security_id"] for candidate in baseline] == [
        "cn-a-share:000003.SZ",
        "cn-a-share:000001.SZ",
        "cn-a-share:000002.SZ",
    ]
    assert main_receipt["schema_version"] == (
        "shallow-gbdt-industry-selection-receipt/v1"
    )
    assert baseline_receipt["schema_version"] == (
        "shallow-gbdt-industry-selection-receipt/v1"
    )
    assert main_receipt["parameters"]["rank_mode"] == (
        "positive_utility_probability"
    )
    assert baseline_receipt["parameters"]["rank_mode"] == "signal_date_amount"
    assert main_receipt["candidate_table_sha256"] == (
        baseline_receipt["candidate_table_sha256"]
    )
    assert main_receipt["candidate_table_sha256"] == ridge._sha256(table)
    _assert_no_key_recursive(
        (table, main, main_receipt, baseline, baseline_receipt),
        "predicted_net_return_pct",
    )


def _passing_fixed_oof_metrics() -> dict[str, object]:
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


def _evaluation_sessions() -> list[str]:
    start = date(2025, 1, 2)
    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range(366)
    ]


def _compact_selection_receipt(receipt: Mapping[str, object]) -> dict[str, object]:
    return ridge._compact_receipt_summary(
        receipt,
        omitted_fields={
            "days": {
                "count_field": "signal_day_count",
                "sha256_field": "days_sha256",
            }
        },
    )


def test_fixed_oof_explicit_ridge_contract_is_legacy_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ridge,
        "_trade_metrics",
        lambda *args, **kwargs: _passing_fixed_oof_metrics(),
    )
    strategy = deepcopy(ridge.CONTINUOUS_RIDGE_OOF_SPEC)
    strategy["advancement_thresholds"]["minimum_complete_trades"] = 1
    candidates = [
        _candidate(
            "cn-a-share:000001.SZ",
            amount=200.0,
            industry="industry-a",
            score=1.25,
            gbdt=False,
        ),
        _candidate(
            "cn-a-share:000002.SZ",
            amount=100.0,
            industry="industry-b",
            score=0.75,
            gbdt=False,
        ),
    ]
    evaluation_sessions = _evaluation_sessions()
    legacy_sweep, legacy_receipt = ridge._evaluate_fixed_oof(
        candidates,
        rank_mode="predicted_net_return",
        evaluation_session_dates=evaluation_sessions,
        strategy_spec=strategy,
        sweep_schema_version="strict-ranked-liquidity-ridge-fixed-oof/v3",
    )
    explicit_sweep, explicit_receipt = ridge._evaluate_fixed_oof(
        candidates,
        rank_mode="predicted_net_return",
        evaluation_session_dates=evaluation_sessions,
        strategy_spec=strategy,
        sweep_schema_version="strict-ranked-liquidity-ridge-fixed-oof/v3",
        score_contract=RIDGE_SCORE_CONTRACT,
    )
    table = ridge._selection_candidate_table(candidates)

    assert explicit_sweep == legacy_sweep
    assert explicit_receipt == legacy_receipt
    assert ridge._recompute_fixed_oof_gate(
        legacy_sweep,
        legacy_receipt,
        table,
        strategy_spec=strategy,
        rank_mode="predicted_net_return",
    ) == ridge._recompute_fixed_oof_gate(
        explicit_sweep,
        explicit_receipt,
        table,
        strategy_spec=strategy,
        rank_mode="predicted_net_return",
        score_contract=RIDGE_SCORE_CONTRACT,
    )
    assert ridge._replay_selection_summary(
        _compact_selection_receipt(legacy_receipt),
        table,
        strategy_spec=strategy,
        rank_mode="predicted_net_return",
    ) == ridge._replay_selection_summary(
        _compact_selection_receipt(explicit_receipt),
        table,
        strategy_spec=strategy,
        rank_mode="predicted_net_return",
        score_contract=RIDGE_SCORE_CONTRACT,
    )


def test_gbdt_fixed_oof_replays_probability_contract_without_ridge_score_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ridge,
        "_trade_metrics",
        lambda *args, **kwargs: _passing_fixed_oof_metrics(),
    )
    strategy = deepcopy(ridge.CONTINUOUS_RIDGE_OOF_SPEC)
    strategy["signal_tag"] = "shallow_gbdt_utility_logit"
    strategy["advancement_thresholds"]["minimum_complete_trades"] = 1
    candidates = [
        _candidate(
            "cn-a-share:000002.SZ",
            amount=100.0,
            industry="industry-b",
            score=0.70,
            gbdt=True,
        ),
        _candidate(
            "cn-a-share:000001.SZ",
            amount=200.0,
            industry="industry-a",
            score=0.80,
            gbdt=True,
        ),
    ]
    table = ridge._selection_candidate_table(
        candidates,
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    sweep, receipt = ridge._evaluate_fixed_oof(
        candidates,
        rank_mode="positive_utility_probability",
        evaluation_session_dates=_evaluation_sessions(),
        strategy_spec=strategy,
        sweep_schema_version=(
            "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
        ),
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert receipt["schema_version"] == (
        "shallow-gbdt-industry-selection-receipt/v1"
    )
    assert receipt["selected_trade_keys"] == [
        "cn-a-share:000001.SZ|2025-01-02|2025-01-03|2025-01-10",
        "cn-a-share:000002.SZ|2025-01-02|2025-01-03|2025-01-10",
    ]
    assert ridge._recompute_fixed_oof_gate(
        sweep,
        receipt,
        table,
        strategy_spec=strategy,
        rank_mode="positive_utility_probability",
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )["target_all_pass"] is True
    assert ridge._replay_selection_summary(
        _compact_selection_receipt(receipt),
        table,
        strategy_spec=strategy,
        rank_mode="positive_utility_probability",
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
    ) == receipt["selected_trade_keys"]
    _assert_no_key_recursive((table, receipt, sweep), "predicted_net_return_pct")


def test_gbdt_fixed_oof_rejects_ridge_rank_mode_even_without_candidates() -> None:
    with pytest.raises(ValueError, match="rank mode"):
        ridge._evaluate_fixed_oof(
            [],
            rank_mode="predicted_net_return",
            evaluation_session_dates=_evaluation_sessions(),
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


def test_gbdt_contract_rejects_nested_ridge_score_field_from_all_evidence_paths() -> None:
    candidate = _candidate(
        "cn-a-share:000001.SZ",
        amount=300.0,
        industry="industry-a",
        score=0.75,
        gbdt=True,
    )
    candidate["execution_evidence"] = {
        "nested": {"predicted_net_return_pct": 1.0}
    }

    for builder in (
        lambda: ridge._outcome_payload_from_scored_candidate(
            candidate,
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        ),
        lambda: ridge._positive_score_pool(
            [candidate],
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        ),
        lambda: ridge._compact_scored_execution_evidence(
            [candidate],
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        ),
    ):
        with pytest.raises(ValueError, match="predicted_net_return_pct"):
            builder()


@pytest.mark.parametrize(
    "contract",
    [
        {**RIDGE_SCORE_CONTRACT, "value": False},
        {**SHALLOW_GBDT_SCORE_CONTRACT, "value": np.float64(0.5)},
    ],
)
def test_score_contract_identity_is_type_sensitive_and_fails_closed(
    contract: Mapping[str, object],
) -> None:
    candidate = _candidate(
        "cn-a-share:000001.SZ",
        amount=300.0,
        industry="industry-a",
        score=0.75,
        gbdt=contract["field"]
        == "predicted_positive_utility_probability",
    )

    with pytest.raises(ValueError, match="frozen contract"):
        candidate_score(candidate, contract=contract)


def test_gbdt_contract_rejects_the_ridge_rank_mode_everywhere() -> None:
    candidate = _candidate(
        "cn-a-share:000001.SZ",
        amount=300.0,
        industry="industry-a",
        score=0.75,
        gbdt=True,
    )

    with pytest.raises(ValueError, match="rank mode"):
        ridge._selection_rank_key(
            candidate,
            rank_mode="predicted_net_return",
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )
    with pytest.raises(ValueError, match="rank mode"):
        ridge._select_with_industry_cap_receipt(
            [candidate],
            rank_mode="predicted_net_return",
            top_n=3,
            max_active_positions=3,
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )
